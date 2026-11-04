"""Recorded meeting capture, consent, Notes handoffs and confirmed PM actions.

This service handles uploaded recordings only. It does not receive a Discord
voice stream or claim live capture. The Notes capability is dispatched through
the existing Store handoff/outbox so the independent Notes runtime must execute
and return its result over Switch before a human can confirm actions.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import UTC, date, datetime
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from .approval import ApprovalWorkflow
from .notes import build_meeting_notes
from .store import ConflictError, Store, encode
from .voice import wav_input


class MeetingAuthorizationError(PermissionError):
    pass


class RecordingJobAuthorizationError(PermissionError):
    pass


_PRIVILEGED_ACTION_TARGETS = {"qa", "reviewer", "release", "publisher", "security"}


class MeetingService:
    def __init__(
        self,
        store: Store,
        provider,
        input_root,
        *,
        pm_id: str,
        notes_agent_id: str,
        specialist_targets: dict[str, Any],
        human_approvers=(),
        engineering_route=None,
        max_audio_bytes=100 * 1024 * 1024,
        max_seconds=3600,
        recording_manifest=None,
        require_recording_authorization=False,
    ):
        if not pm_id or not notes_agent_id or pm_id == notes_agent_id:
            raise ValueError("Distinct PM and Notes Switch identities are required")
        if not 1024 <= max_audio_bytes <= 100 * 1024 * 1024 or not 1 <= max_seconds <= 3600:
            raise ValueError("Meeting audio bounds are invalid")
        self.store, self.provider = store, provider
        self.input_root = Path(input_root)
        self.pm_id, self.notes_agent_id = pm_id, notes_agent_id
        self.human_approvers = set(human_approvers)
        self.engineering_route = dict(engineering_route or {})
        self.max_audio_bytes, self.max_seconds = max_audio_bytes, max_seconds
        if type(require_recording_authorization) is not bool:
            raise ValueError("Recording authorization mode must be a boolean")
        self.require_recording_authorization = require_recording_authorization
        self.recording_manifest = self._validate_recording_manifest(recording_manifest or {})
        self.targets = {}
        for role, target in specialist_targets.items():
            if isinstance(target, str):
                agent_id = target
            elif isinstance(target, dict):
                agent_id = target.get("agent_id")
            else:
                agent_id = None
            if not isinstance(agent_id, str) or not agent_id.strip():
                raise ValueError("Specialist target identities must be configured")
            self.targets[role] = agent_id
        self.approvals = ApprovalWorkflow(store, self.human_approvers)
        with store._lock:
            store.connection.executescript("""
            CREATE TABLE IF NOT EXISTS meetings(
              id TEXT PRIMARY KEY, channel_id TEXT NOT NULL, thread_id TEXT,
              created_by TEXT NOT NULL, source TEXT NOT NULL, status TEXT NOT NULL,
              transcription_state TEXT NOT NULL DEFAULT 'pending', input_sha256 TEXT,
              transcript_json TEXT, transcript_sha256 TEXT, provider_json TEXT,
              notes_json TEXT, notes_sha256 TEXT, notes_task_id TEXT, notes_handoff_id TEXT,
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS meeting_participants(
              meeting_id TEXT NOT NULL REFERENCES meetings(id), participant_id TEXT NOT NULL,
              consent_evidence TEXT, consent_expires_at TEXT, consent_granted_at TEXT,
              consent_revoked_at TEXT, present INTEGER NOT NULL DEFAULT 0,
              first_joined_at TEXT, last_left_at TEXT, absent_seconds REAL NOT NULL DEFAULT 0,
              reconnect_count INTEGER NOT NULL DEFAULT 0,
              PRIMARY KEY(meeting_id, participant_id)
            );
            CREATE TABLE IF NOT EXISTS meeting_presence_events(
              meeting_id TEXT NOT NULL REFERENCES meetings(id), event_id TEXT NOT NULL,
              participant_id TEXT NOT NULL, event_type TEXT NOT NULL, occurred_at TEXT NOT NULL,
              absent_seconds REAL, PRIMARY KEY(meeting_id, event_id)
            );
            CREATE TABLE IF NOT EXISTS meeting_action_proposals(
              meeting_id TEXT NOT NULL REFERENCES meetings(id), proposal_id TEXT NOT NULL,
              data TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'proposed', task_id TEXT,
              confirmation_data TEXT, confirmed_by TEXT, confirmed_at TEXT,
              PRIMARY KEY(meeting_id, proposal_id)
            );
            CREATE TABLE IF NOT EXISTS meeting_recording_jobs(
              id TEXT PRIMARY KEY, meeting_id TEXT NOT NULL REFERENCES meetings(id),
              task_id TEXT NOT NULL UNIQUE REFERENCES tasks(id),
              handoff_id TEXT NOT NULL UNIQUE REFERENCES handoffs(id),
              voice_agent_id TEXT NOT NULL, filename TEXT NOT NULL, input_sha256 TEXT NOT NULL,
              requested_by TEXT NOT NULL, status TEXT NOT NULL, transcript_sha256 TEXT,
              provider_json TEXT, result_json TEXT,
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              UNIQUE(meeting_id)
            );
            CREATE TABLE IF NOT EXISTS meeting_audit_events(
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              meeting_id TEXT NOT NULL REFERENCES meetings(id),
              event_type TEXT NOT NULL, actor TEXT NOT NULL, details TEXT NOT NULL,
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """)

    def _audit(self, meeting_id, event_type, actor, details):
        self.store.connection.execute(
            "INSERT INTO meeting_audit_events(meeting_id,event_type,actor,details) VALUES(?,?,?,?)",
            (meeting_id, event_type, actor, encode(details)),
        )

    def _meeting(self, meeting_id):
        row = self.store.connection.execute(
            "SELECT * FROM meetings WHERE id=?", (meeting_id,)
        ).fetchone()
        if row is None:
            raise KeyError(meeting_id)
        value = dict(row)
        for key in ("source", "transcript_json", "provider_json", "notes_json"):
            value[key.removesuffix("_json")] = (
                json.loads(value.pop(key)) if value.get(key) else None
            )
        return value

    def _actor(self, meeting, actor):
        if (
            not isinstance(actor, str)
            or not actor
            or (actor != meeting["created_by"] and actor not in self.human_approvers)
        ):
            raise MeetingAuthorizationError("Authenticated meeting organizer is required")

    def _consents(self, meeting_id, *, now=None):
        now = now or datetime.now(UTC)
        rows = self.store.connection.execute(
            "SELECT * FROM meeting_participants WHERE meeting_id=? ORDER BY participant_id",
            (meeting_id,),
        ).fetchall()
        if not rows:
            raise MeetingAuthorizationError("Meeting participant roster is empty")
        invalid = []
        grants = []
        for row in rows:
            try:
                expiry = datetime.fromisoformat(row["consent_expires_at"])
                active = (
                    row["consent_evidence"]
                    and row["consent_granted_at"]
                    and row["consent_revoked_at"] is None
                    and expiry.tzinfo is not None
                    and expiry > now
                )
            except (TypeError, ValueError):
                active = False
            if not active:
                invalid.append(row["participant_id"])
            else:
                grants.append(
                    {
                        "participant_id": row["participant_id"],
                        "evidence": row["consent_evidence"],
                        "expires_at": row["consent_expires_at"],
                    }
                )
        if invalid:
            raise MeetingAuthorizationError("Current consent is required from every participant")
        return grants

    def create_meeting(
        self,
        meeting_id,
        channel_id,
        thread_id,
        authenticated_actor,
        participants,
        *,
        source=None,
    ):
        if not isinstance(meeting_id, str) or not meeting_id.strip() or len(meeting_id) > 200:
            raise ValueError("A bounded meeting ID is required")
        if not isinstance(channel_id, str) or not channel_id.strip():
            raise ValueError("A Discord channel ID is required")
        if not isinstance(participants, list) or not 1 <= len(participants) <= 100:
            raise ValueError("A meeting needs a bounded participant roster")
        if (
            any(not isinstance(p, str) or not p.strip() for p in participants)
            or len(participants) != len(set(participants))
            or authenticated_actor not in participants
        ):
            raise MeetingAuthorizationError("Creator must be a listed participant")
        participants = sorted(participants)
        source = dict(source or {})
        source.update(channel_id=channel_id, thread_id=thread_id, user_id=authenticated_actor)
        if source.get("room_id") is not None and not isinstance(source["room_id"], str):
            raise ValueError("Switch room ID must be a string")
        with self.store._lock, self.store.transaction():
            old = self.store.connection.execute(
                "SELECT * FROM meetings WHERE id=?", (meeting_id,)
            ).fetchone()
            immutable = (channel_id, thread_id, authenticated_actor, encode(source))
            if old:
                previous = (old["channel_id"], old["thread_id"], old["created_by"], old["source"])
                old_participants = [
                    row[0]
                    for row in self.store.connection.execute(
                        "SELECT participant_id FROM meeting_participants WHERE meeting_id=? "
                        "ORDER BY participant_id",
                        (meeting_id,),
                    )
                ]
                if previous != immutable or old_participants != participants:
                    raise ConflictError("Meeting ID reused with different source or participants")
                return self._meeting(meeting_id)
            self.store.connection.execute(
                "INSERT INTO meetings(id,channel_id,thread_id,created_by,source,status) "
                "VALUES(?,?,?,?,?,'created')",
                (meeting_id, channel_id, thread_id, authenticated_actor, encode(source)),
            )
            self.store.connection.executemany(
                "INSERT INTO meeting_participants(meeting_id,participant_id) VALUES(?,?)",
                [(meeting_id, participant) for participant in participants],
            )
            self._audit(meeting_id, "meeting.created", authenticated_actor, {"source": source})
        return self._meeting(meeting_id)

    def grant_consent(
        self,
        meeting_id,
        participant_id,
        authenticated_actor,
        *,
        evidence_id,
        expires_at,
        disclosure_acknowledged,
    ):
        if authenticated_actor != participant_id:
            raise MeetingAuthorizationError("Only the authenticated participant can consent")
        if disclosure_acknowledged is not True:
            raise MeetingAuthorizationError(
                "Recording and transcription disclosure must be accepted"
            )
        if not isinstance(evidence_id, str) or not evidence_id.strip():
            raise ValueError("Consent evidence ID is required")
        expiry = datetime.fromisoformat(expires_at)
        if expiry.tzinfo is None or expiry <= datetime.now(UTC):
            raise ValueError("Consent must expire in the future and include a timezone")
        with self.store._lock, self.store.transaction():
            self._meeting(meeting_id)
            row = self.store.connection.execute(
                "SELECT * FROM meeting_participants WHERE meeting_id=? AND participant_id=?",
                (meeting_id, participant_id),
            ).fetchone()
            if row is None:
                raise MeetingAuthorizationError("Participant is not in this meeting")
            if row["consent_revoked_at"]:
                raise ConflictError("A revoked meeting cannot be reconsented; start a new meeting")
            if row["consent_granted_at"]:
                if (
                    row["consent_evidence"] == evidence_id
                    and row["consent_expires_at"] == expiry.isoformat()
                ):
                    return False
                raise ConflictError("Consent already recorded for this meeting participant")
            self.store.connection.execute(
                "UPDATE meeting_participants SET consent_evidence=?,consent_expires_at=?,"
                "consent_granted_at=? WHERE meeting_id=? AND participant_id=?",
                (
                    evidence_id,
                    expiry.isoformat(),
                    datetime.now(UTC).isoformat(),
                    meeting_id,
                    participant_id,
                ),
            )
            self._audit(
                meeting_id,
                "consent.granted",
                authenticated_actor,
                {"participant_id": participant_id, "evidence_id": evidence_id},
            )
        return True

    def revoke_consent(self, meeting_id, participant_id, authenticated_actor):
        if authenticated_actor != participant_id:
            raise MeetingAuthorizationError("Only the authenticated participant can revoke consent")
        with self.store._lock, self.store.transaction():
            self._meeting(meeting_id)
            row = self.store.connection.execute(
                "SELECT * FROM meeting_participants WHERE meeting_id=? AND participant_id=?",
                (meeting_id, participant_id),
            ).fetchone()
            if row is None or not row["consent_granted_at"]:
                raise MeetingAuthorizationError("An active participant consent grant is required")
            if row["consent_revoked_at"]:
                return False
            revoked_at = datetime.now(UTC).isoformat()
            self.store.connection.execute(
                "UPDATE meeting_participants SET consent_revoked_at=? "
                "WHERE meeting_id=? AND participant_id=?",
                (revoked_at, meeting_id, participant_id),
            )
            self.store.connection.execute(
                "UPDATE meetings SET status='consent_revoked',transcription_state='revoked',"
                "transcript_json=NULL,transcript_sha256=NULL,provider_json=NULL,notes_json=NULL,"
                "notes_sha256=NULL,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (meeting_id,),
            )
            jobs = self.store.connection.execute(
                "SELECT task_id FROM meeting_recording_jobs "
                "WHERE meeting_id=? AND status NOT IN ('cancelled','revoked')",
                (meeting_id,),
            ).fetchall()
            self.store.connection.execute(
                "UPDATE meeting_recording_jobs SET status='revoked',transcript_sha256=NULL,"
                "provider_json=NULL,result_json=NULL,updated_at=CURRENT_TIMESTAMP "
                "WHERE meeting_id=?",
                (meeting_id,),
            )
            task_ids = {row["task_id"] for row in jobs}
            if self._meeting(meeting_id)["notes_task_id"]:
                task_ids.add(self._meeting(meeting_id)["notes_task_id"])
            for task_id in task_ids:
                task = self.store.get_task(task_id)
                if task and task["status"] not in {"CANCELLED", "COMPLETED", "FAILED"}:
                    self.store.transition_task(task_id, "CANCELLED", task["version"])
            action_rows = self.store.connection.execute(
                "SELECT task_id FROM meeting_action_proposals WHERE meeting_id=? "
                "AND task_id IS NOT NULL",
                (meeting_id,),
            ).fetchall()
            for action_row in action_rows:
                task = self.store.get_task(action_row["task_id"])
                if task and task["status"] not in {"CANCELLED", "COMPLETED", "FAILED"}:
                    self.store.transition_task(task["id"], "CANCELLED", task["version"])
            self.store.connection.execute(
                "UPDATE meeting_action_proposals SET status='revoked' WHERE meeting_id=? "
                "AND status IN ('proposed','confirmed')",
                (meeting_id,),
            )
            self._audit(meeting_id, "consent.revoked", authenticated_actor, {"at": revoked_at})
        return True

    def record_presence_event(
        self,
        meeting_id,
        event_id,
        authenticated_participant_id,
        event_type,
        *,
        occurred_at=None,
    ):
        if event_type not in {"join", "leave"} or not isinstance(event_id, str) or not event_id:
            raise ValueError("Authenticated join/leave event identity is required")
        when = datetime.fromisoformat(occurred_at) if occurred_at else datetime.now(UTC)
        if when.tzinfo is None:
            raise ValueError("Presence event timestamp must include a timezone")
        when_text = when.astimezone(UTC).isoformat()
        with self.store._lock, self.store.transaction():
            self._meeting(meeting_id)
            row = self.store.connection.execute(
                "SELECT * FROM meeting_participants WHERE meeting_id=? AND participant_id=?",
                (meeting_id, authenticated_participant_id),
            ).fetchone()
            if row is None:
                raise MeetingAuthorizationError("Authenticated user is not a meeting participant")
            previous = self.store.connection.execute(
                "SELECT * FROM meeting_presence_events WHERE meeting_id=? AND event_id=?",
                (meeting_id, event_id),
            ).fetchone()
            if previous:
                if (
                    previous["participant_id"] != authenticated_participant_id
                    or previous["event_type"]
                    not in (
                        {"joined", "reconnected", "still_present"}
                        if event_type == "join"
                        else {"left", "already_absent"}
                    )
                    or previous["occurred_at"] != when_text
                ):
                    raise ConflictError("Presence event ID reused with different content")
                return {"duplicate": True, **dict(previous)}
            absent_seconds = None
            if event_type == "join":
                if row["present"]:
                    normalized_type = "still_present"
                elif row["last_left_at"]:
                    normalized_type = "reconnected"
                    absent_seconds = max(
                        0.0,
                        (when - datetime.fromisoformat(row["last_left_at"])).total_seconds(),
                    )
                    self.store.connection.execute(
                        "UPDATE meeting_participants SET present=1,"
                        "reconnect_count=reconnect_count+1,"
                        "absent_seconds=absent_seconds+? WHERE meeting_id=? AND participant_id=?",
                        (absent_seconds, meeting_id, authenticated_participant_id),
                    )
                else:
                    normalized_type = "joined"
                    self.store.connection.execute(
                        "UPDATE meeting_participants SET present=1,first_joined_at=? "
                        "WHERE meeting_id=? AND participant_id=?",
                        (when_text, meeting_id, authenticated_participant_id),
                    )
            else:
                normalized_type = "left" if row["present"] else "already_absent"
                if row["present"]:
                    self.store.connection.execute(
                        "UPDATE meeting_participants SET present=0,last_left_at=? "
                        "WHERE meeting_id=? AND participant_id=?",
                        (when_text, meeting_id, authenticated_participant_id),
                    )
            self.store.connection.execute(
                "INSERT INTO meeting_presence_events(meeting_id,event_id,participant_id,event_type,"
                "occurred_at,absent_seconds) VALUES(?,?,?,?,?,?)",
                (
                    meeting_id,
                    event_id,
                    authenticated_participant_id,
                    normalized_type,
                    when_text,
                    absent_seconds,
                ),
            )
            self._audit(
                meeting_id,
                "presence." + normalized_type,
                authenticated_participant_id,
                {"event_id": event_id, "occurred_at": when_text, "absent_seconds": absent_seconds},
            )
        return {
            "duplicate": False,
            "event_id": event_id,
            "participant_id": authenticated_participant_id,
            "event_type": normalized_type,
            "occurred_at": when_text,
            "absent_seconds": absent_seconds,
        }

    def cancel_meeting(self, meeting_id, authenticated_actor):
        with self.store._lock, self.store.transaction():
            meeting = self._meeting(meeting_id)
            self._actor(meeting, authenticated_actor)
            if meeting["status"] == "cancelled":
                return meeting
            task_ids = [meeting["notes_task_id"]]
            recording_jobs = self.store.connection.execute(
                "SELECT task_id FROM meeting_recording_jobs WHERE meeting_id=? "
                "AND status NOT IN ('cancelled','revoked')",
                (meeting_id,),
            ).fetchall()
            task_ids.extend(row["task_id"] for row in recording_jobs)
            task_ids.extend(
                row["task_id"]
                for row in self.store.connection.execute(
                    "SELECT task_id FROM meeting_action_proposals "
                    "WHERE meeting_id=? AND task_id IS NOT NULL",
                    (meeting_id,),
                )
            )
            for task_id in task_ids:
                task = self.store.get_task(task_id) if task_id else None
                if task and task["status"] not in {"CANCELLED", "COMPLETED", "FAILED"}:
                    self.store.transition_task(task_id, "CANCELLED", task["version"])
            self.store.connection.execute(
                "UPDATE meeting_recording_jobs SET status='cancelled',result_json=NULL,"
                "updated_at=CURRENT_TIMESTAMP WHERE meeting_id=? "
                "AND status NOT IN ('completed','revoked')",
                (meeting_id,),
            )
            self.store.connection.execute(
                "UPDATE meetings SET status='cancelled',updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (meeting_id,),
            )
            self._audit(meeting_id, "meeting.cancelled", authenticated_actor, {})
        return self._meeting(meeting_id)

    def _recording_input(self, filename):
        relative = self._recording_relative(filename)
        root = self.input_root.resolve(strict=True)
        audio = wav_input(
            root,
            relative,
            max_bytes=self.max_audio_bytes,
            max_seconds=self.max_seconds,
        )
        return relative, audio, hashlib.sha256(audio).hexdigest()

    def _recording_relative(self, filename):
        if not isinstance(filename, str) or not filename.strip() or len(filename) > 1000:
            raise ValueError("A bounded recording file name is required")
        root = self.input_root.resolve(strict=True)
        path = (root / filename).resolve(strict=True)
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError("Recording must be inside the configured upload root")
        return path.relative_to(root).as_posix()

    @staticmethod
    def _validate_recording_manifest(manifest):
        if not isinstance(manifest, dict):
            raise ValueError("Operator recording manifest must be an object")
        result = {}
        for filename, entry in manifest.items():
            path = PurePosixPath(filename) if isinstance(filename, str) else None
            if (
                path is None
                or not filename
                or path.is_absolute()
                or any(part in {"", ".", ".."} for part in path.parts)
                or path.as_posix() != filename
                or not isinstance(entry, dict)
                or not isinstance(entry.get("meeting_id"), str)
                or not entry["meeting_id"].strip()
                or not isinstance(entry.get("owner_id"), str)
                or not entry["owner_id"].strip()
                or not isinstance(entry.get("participants"), list)
                or not entry["participants"]
                or any(
                    not isinstance(person, str) or not person for person in entry["participants"]
                )
                or len(set(entry["participants"])) != len(entry["participants"])
                or (
                    entry.get("sha256") is not None
                    and (
                        not isinstance(entry["sha256"], str)
                        or len(entry["sha256"]) != 64
                        or any(char not in "0123456789abcdef" for char in entry["sha256"])
                    )
                )
            ):
                raise ValueError("Operator recording manifest entry is invalid")
            result[filename] = {
                "meeting_id": entry["meeting_id"],
                "owner_id": entry["owner_id"],
                "participants": tuple(entry["participants"]),
                "sha256": entry.get("sha256"),
            }
        return result

    def _authorize_recording(self, meeting, actor, filename, input_digest):
        if not self.require_recording_authorization:
            return
        entry = self.recording_manifest.get(filename)
        participants = tuple(
            row["participant_id"]
            for row in self.store.connection.execute(
                "SELECT participant_id FROM meeting_participants WHERE meeting_id=? "
                "ORDER BY participant_id",
                (meeting["id"],),
            )
        )
        if (
            not entry
            or entry["meeting_id"] != meeting["id"]
            or entry["owner_id"] not in participants
            or actor not in {entry["owner_id"], *self.human_approvers}
            or set(entry["participants"]) != set(participants)
            or (
                input_digest is not None
                and entry["sha256"]
                and entry["sha256"] != input_digest
            )
        ):
            raise MeetingAuthorizationError(
                "Operator recording ownership and exact participant manifest are required"
            )

    def request_transcription(
        self,
        meeting_id,
        filename,
        authenticated_actor,
        *,
        voice_agent_id,
    ):
        """Persist a PM-to-Voice job; this method never calls the STT provider."""
        if (
            not isinstance(voice_agent_id, str)
            or not voice_agent_id.strip()
            or voice_agent_id in {self.pm_id, self.notes_agent_id, *self.targets.values()}
        ):
            raise ValueError("A distinct configured Voice Switch identity is required")
        with self.store._lock:
            meeting = self._meeting(meeting_id)
            self._actor(meeting, authenticated_actor)
            if meeting["status"] in {"cancelled", "consent_revoked"}:
                raise MeetingAuthorizationError("Meeting is cancelled or consent was revoked")
            self._consents(meeting_id)
        relative_filename = self._recording_relative(filename)
        self._authorize_recording(meeting, authenticated_actor, relative_filename, None)
        relative_filename, _audio, input_digest = self._recording_input(relative_filename)
        self._authorize_recording(meeting, authenticated_actor, relative_filename, input_digest)
        with self.store._lock, self.store.transaction():
            meeting = self._meeting(meeting_id)
            self._actor(meeting, authenticated_actor)
            if meeting["status"] in {"cancelled", "consent_revoked"}:
                raise MeetingAuthorizationError("Meeting is cancelled or consent was revoked")
            self._consents(meeting_id)
            if not meeting["source"].get("room_id"):
                raise ValueError("A verified Switch room_id is required for Voice handoff")
            existing = self.store.connection.execute(
                "SELECT * FROM meeting_recording_jobs WHERE meeting_id=?", (meeting_id,)
            ).fetchone()
            if existing:
                if (
                    existing["input_sha256"] != input_digest
                    or existing["voice_agent_id"] != voice_agent_id
                ):
                    raise ConflictError("Meeting already has a different recording job")
                return self._recording_job_public(existing)
            job_id = str(
                uuid5(NAMESPACE_URL, f"switchcrew:meeting:{meeting_id}:recording:{input_digest}")
            )
            task_id = str(uuid5(NAMESPACE_URL, f"{job_id}:task"))
            handoff_id = str(uuid5(NAMESPACE_URL, f"{job_id}:voice-handoff"))
            task_source = {
                **meeting["source"],
                "kind": "meeting_transcription",
                "meeting_id": meeting_id,
                "recording_job_id": job_id,
            }
            task = self.store.create_task(
                f"Transcribe consented meeting recording {meeting_id}",
                task_source,
                [
                    "Return a timestamped transcript using only the registered recording job.",
                    "Keep provider speaker labels anonymous and include recording provenance.",
                ],
                task_id=task_id,
            )
            task = self.store.transition_task(task_id, "PLANNED", task["version"])
            task = self.store.transition_task(task_id, "IN_PROGRESS", task["version"])
            self.store.create_handoff(
                task_id,
                self.pm_id,
                voice_agent_id,
                f"Transcribe registered meeting recording job {job_id}.",
                ["Use only the registered, consent-checked audio artifact."],
                task["version"],
                handoff_id=handoff_id,
            )
            self.store.connection.execute(
                "INSERT INTO meeting_recording_jobs(id,meeting_id,task_id,handoff_id,"
                "voice_agent_id,filename,input_sha256,requested_by,status) "
                "VALUES(?,?,?,?,?,?,?,?, 'queued')",
                (
                    job_id,
                    meeting_id,
                    task_id,
                    handoff_id,
                    voice_agent_id,
                    relative_filename,
                    input_digest,
                    authenticated_actor,
                ),
            )
            self.store.connection.execute(
                "UPDATE meetings SET status='transcription_queued',"
                "transcription_state='queued',input_sha256=?,updated_at=CURRENT_TIMESTAMP "
                "WHERE id=?",
                (input_digest, meeting_id),
            )
            self._audit(
                meeting_id,
                "transcription.handoff_created",
                authenticated_actor,
                {"recording_job_id": job_id, "task_id": task_id, "handoff_id": handoff_id},
            )
            job = self.store.connection.execute(
                "SELECT * FROM meeting_recording_jobs WHERE id=?", (job_id,)
            ).fetchone()
            return self._recording_job_public(job)

    @staticmethod
    def _recording_job_public(row):
        return {
            "status": row["status"],
            "meeting_id": row["meeting_id"],
            "recording_job_id": row["id"],
            "task_id": row["task_id"],
            "handoff_id": row["handoff_id"],
            "transcript_sha256": row["transcript_sha256"],
        }

    async def transcribe_for_handoff(self, state, authenticated_voice_agent):
        task_id, handoff_id = state.get("task_id"), state.get("handoff_id")
        with self.store._lock:
            task = self.store.get_task(task_id)
            handoff = self.store.get_handoff(handoff_id)
            if (
                not task
                or not handoff
                or handoff["task_id"] != task_id
                or handoff["recipient"] != authenticated_voice_agent
                or handoff["status"] not in {"accepted", "completed"}
                or ("question" in state and state["question"] != handoff["question"])
                or (
                    "acceptance_criteria" in state
                    and state["acceptance_criteria"] != handoff["acceptance_criteria"]
                )
            ):
                return {"status": "blocked", "reason": "addressed_voice_handoff_required"}
            job_id = task["source"].get("recording_job_id")
            meeting_id = task["source"].get("meeting_id")
            row = self.store.connection.execute(
                "SELECT * FROM meeting_recording_jobs WHERE id=?", (job_id,)
            ).fetchone()
            if (
                not row
                or row["meeting_id"] != meeting_id
                or row["task_id"] != task_id
                or row["handoff_id"] != handoff_id
                or row["voice_agent_id"] != authenticated_voice_agent
            ):
                return {"status": "blocked", "reason": "registered_voice_job_required"}
            meeting = self._meeting(meeting_id)
            if meeting["status"] in {"cancelled", "consent_revoked"}:
                return {"status": "blocked", "reason": "meeting_cancelled_or_consent_revoked"}
            if task["status"] not in {"PLANNED", "IN_PROGRESS", "COMPLETED"}:
                return {"status": "blocked", "reason": "voice_task_not_active"}
            try:
                self._consents(meeting_id)
            except MeetingAuthorizationError:
                return {"status": "blocked", "reason": "current_participant_consent_required"}
            if handoff["status"] == "completed" and handoff["result"]:
                return handoff["result"]
            if task["status"] not in {"PLANNED", "IN_PROGRESS"}:
                return {"status": "blocked", "reason": "voice_task_not_active"}
            if row["status"] == "completed" and row["result_json"]:
                return json.loads(row["result_json"])
            if row["status"] in {"in_progress", "uncertain"}:
                return {"status": "pending_execution", "reason": "voice_outcome_uncertain"}
            if row["status"] != "queued":
                return {"status": "blocked", "reason": "recording_job_not_active"}
            with self.store.transaction():
                self.store.connection.execute(
                    "UPDATE meeting_recording_jobs SET status='in_progress',"
                    "updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='queued'",
                    (job_id,),
                )
                self._audit(
                    meeting_id,
                    "transcription.started",
                    authenticated_voice_agent,
                    {"recording_job_id": job_id},
                )

        def authorize_job():
            current_task = self.store.get_task(task_id)
            current_handoff = self.store.get_handoff(handoff_id)
            current_job = self.store.connection.execute(
                "SELECT status,voice_agent_id FROM meeting_recording_jobs WHERE id=?",
                (job_id,),
            ).fetchone()
            if (
                not current_task
                or current_task["status"] not in {"PLANNED", "IN_PROGRESS"}
                or not current_handoff
                or current_handoff["status"] not in {"accepted", "completed"}
                or current_handoff["recipient"] != authenticated_voice_agent
                or not current_job
                or current_job["status"] != "in_progress"
                or current_job["voice_agent_id"] != authenticated_voice_agent
            ):
                raise RecordingJobAuthorizationError("Voice assignment was cancelled or revoked")

        try:
            result = await self.transcribe_recording(
                meeting_id,
                row["filename"],
                row["requested_by"],
                expected_sha256=row["input_sha256"],
                recording_job_id=job_id,
                authorization_check=authorize_job,
            )
        except asyncio.CancelledError:
            with self.store._lock, self.store.transaction():
                self.store.connection.execute(
                    "UPDATE meeting_recording_jobs SET status='uncertain',"
                    "updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='in_progress'",
                    (job_id,),
                )
            raise
        if result.get("status") == "completed":
            transcript = result["transcript"]
            provenance = transcript.get("recording_provenance", {})
            public_result = {
                "status": "completed",
                "role": "voice",
                "meeting_id": meeting_id,
                "recording_job_id": job_id,
                "transcript_sha256": result["transcript_sha256"],
                "provider": result["provider"],
                "recording_provenance": provenance,
            }
            job_status = "completed"
        else:
            public_result = {
                "status": result.get("status", "blocked"),
                "role": "voice",
                "meeting_id": meeting_id,
                "recording_job_id": job_id,
                "reason": result.get("reason", "recording_transcription_unavailable"),
            }
            job_status = (
                "uncertain"
                if result.get("status") == "pending_execution"
                else "revoked"
                if result.get("reason") == "consent_changed_during_transcription"
                else "blocked"
            )
        with self.store._lock, self.store.transaction():
            current = self._meeting(meeting_id)
            current_task = self.store.get_task(task_id)
            current_handoff = self.store.get_handoff(handoff_id)
            current_job = self.store.connection.execute(
                "SELECT status FROM meeting_recording_jobs WHERE id=?", (job_id,)
            ).fetchone()
            if current["status"] in {"cancelled", "consent_revoked"} or current_job["status"] in {
                "cancelled",
                "revoked",
            }:
                public_result = {
                    "status": "blocked",
                    "role": "voice",
                    "meeting_id": meeting_id,
                    "recording_job_id": job_id,
                    "reason": "meeting_cancelled_or_consent_revoked",
                }
                job_status = current_job["status"]
            elif (
                not current_task
                or current_task["status"] not in {"PLANNED", "IN_PROGRESS"}
                or not current_handoff
                or current_handoff["status"] not in {"accepted", "completed"}
            ):
                self.store.connection.execute(
                    "UPDATE meetings SET transcript_json=NULL,transcript_sha256=NULL,"
                    "provider_json=NULL,notes_json=NULL,notes_sha256=NULL,"
                    "status='transcription_cancelled',transcription_state='cancelled',"
                    "updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (meeting_id,),
                )
                public_result = {
                    "status": "blocked",
                    "role": "voice",
                    "meeting_id": meeting_id,
                    "recording_job_id": job_id,
                    "reason": "voice_task_cancelled_or_unassigned",
                }
                job_status = "blocked"
            self.store.connection.execute(
                "UPDATE meeting_recording_jobs SET status=?,transcript_sha256=?,"
                "provider_json=?,result_json=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (
                    job_status,
                    public_result.get("transcript_sha256"),
                    encode(public_result["provider"]) if public_result.get("provider") else None,
                    encode(public_result),
                    job_id,
                ),
            )
            self._audit(
                meeting_id,
                "transcription.finished",
                authenticated_voice_agent,
                {"recording_job_id": job_id, "status": public_result["status"]},
            )
        return public_result

    def _write_transcript(
        self,
        meeting_id,
        actor,
        filename,
        *,
        expected_sha256=None,
        recording_job_id=None,
    ):
        with self.store._lock:
            meeting = self._meeting(meeting_id)
            self._actor(meeting, actor)
            if meeting["status"] in {"cancelled", "consent_revoked"}:
                return {"status": "blocked", "reason": "meeting_cancelled_or_consent_revoked"}
            recording_job = self.store.connection.execute(
                "SELECT id,status FROM meeting_recording_jobs WHERE meeting_id=?",
                (meeting_id,),
            ).fetchone()
            if self.require_recording_authorization and recording_job is None:
                return {"status": "blocked", "reason": "voice_switch_handoff_required"}
            if recording_job and recording_job["id"] != recording_job_id:
                return {"status": "blocked", "reason": "voice_switch_handoff_required"}
            if recording_job and recording_job["status"] not in {"queued", "in_progress"}:
                return {"status": "pending_execution", "reason": "recording_job_not_retriable"}
            try:
                consent = self._consents(meeting_id)
            except MeetingAuthorizationError:
                return {"status": "blocked", "reason": "current_participant_consent_required"}
            if self.provider is None or not callable(
                getattr(self.provider, "transcribe_meeting", None)
            ):
                return {"status": "blocked_configuration", "reason": "meeting_stt_unavailable"}
            relative_filename = self._recording_relative(filename)
            try:
                self._authorize_recording(meeting, actor, relative_filename, None)
            except MeetingAuthorizationError:
                return {
                    "status": "blocked",
                    "reason": "operator_recording_manifest_required",
                }
            relative_filename, audio, input_digest = self._recording_input(relative_filename)
            try:
                self._authorize_recording(meeting, actor, relative_filename, input_digest)
            except MeetingAuthorizationError:
                return {
                    "status": "blocked",
                    "reason": "operator_recording_manifest_required",
                }
            if expected_sha256 and input_digest != expected_sha256:
                return {"status": "blocked", "reason": "recording_changed_after_request"}
            if meeting["transcript"]:
                if meeting["input_sha256"] != input_digest:
                    raise ConflictError(
                        "Meeting already has a transcript for a different recording"
                    )
                return self._public_transcript(meeting)
            if meeting["transcription_state"] in {"in_progress", "uncertain", "revoked"}:
                return {
                    "status": "pending_execution",
                    "reason": "meeting_transcription_not_retriable",
                }
            with self.store.transaction():
                self.store.connection.execute(
                    "UPDATE meetings SET status='transcribing',transcription_state='in_progress',"
                    "input_sha256=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (input_digest, meeting_id),
                )
                self._audit(
                    meeting_id, "transcription.reserved", actor, {"input_sha256": input_digest}
                )
            return audio, input_digest, consent

    @staticmethod
    def _public_transcript(meeting):
        return {
            "status": "completed",
            "meeting_id": meeting["id"],
            "transcript_sha256": meeting["transcript_sha256"],
            "transcript": meeting["transcript"],
            "provider": meeting["provider"],
        }

    async def transcribe_recording(
        self,
        meeting_id,
        filename,
        authenticated_actor,
        *,
        expected_sha256=None,
        recording_job_id=None,
        authorization_check=None,
    ):
        if self.require_recording_authorization and (
            recording_job_id is None or not callable(authorization_check)
        ):
            return {"status": "blocked", "reason": "voice_switch_handoff_required"}
        reserved = self._write_transcript(
            meeting_id,
            authenticated_actor,
            filename,
            expected_sha256=expected_sha256,
            recording_job_id=recording_job_id,
        )
        if isinstance(reserved, dict):
            return reserved
        audio, input_digest, grants = reserved
        try:
            transcript = await self.provider.transcribe_meeting(audio)
            if not isinstance(transcript, dict) or not isinstance(transcript.get("words"), list):
                raise ValueError("Timestamped meeting transcript is invalid")
            transcript = dict(transcript)
            transcript["recording_provenance"] = {
                "input_sha256": input_digest,
                "source": "operator_uploaded_recording",
                "capture_mode": "recorded_file",
            }
            transcript["speaker_identity_policy"] = (
                "Provider speaker IDs are anonymous acoustic labels; no attendee mapping inferred."
            )
            provider = (
                self.provider.spec()
                if callable(getattr(self.provider, "spec", None))
                else {"provider": "injected_test_or_operator_provider"}
            )
            transcript_digest = hashlib.sha256(encode(transcript).encode()).hexdigest()
            with self.store._lock, self.store.transaction():
                meeting = self._meeting(meeting_id)
                if meeting["status"] in {"cancelled", "consent_revoked"}:
                    return {"status": "blocked", "reason": "meeting_cancelled_or_consent_revoked"}
                current_grants = self._consents(meeting_id)
                if current_grants != grants:
                    raise MeetingAuthorizationError(
                        "Consent changed while transcription was running"
                    )
                if authorization_check:
                    authorization_check()
                self.store.connection.execute(
                    "UPDATE meetings SET status='transcribed',transcription_state='completed',"
                    "transcript_json=?,transcript_sha256=?,provider_json=?,"
                    "updated_at=CURRENT_TIMESTAMP "
                    "WHERE id=?",
                    (encode(transcript), transcript_digest, encode(provider), meeting_id),
                )
                self._audit(
                    meeting_id,
                    "transcription.completed",
                    authenticated_actor,
                    {"transcript_sha256": transcript_digest, "provider": provider},
                )
            return self._public_transcript(self._meeting(meeting_id))
        except MeetingAuthorizationError:
            with self.store._lock, self.store.transaction():
                self.store.connection.execute(
                    "UPDATE meetings SET status='consent_revoked',transcription_state='revoked',"
                    "updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (meeting_id,),
                )
            return {"status": "blocked", "reason": "consent_changed_during_transcription"}
        except RecordingJobAuthorizationError:
            with self.store._lock, self.store.transaction():
                self.store.connection.execute(
                    "UPDATE meetings SET status='transcription_cancelled',"
                    "transcription_state='cancelled',updated_at=CURRENT_TIMESTAMP "
                    "WHERE id=? AND status NOT IN ('cancelled','consent_revoked')",
                    (meeting_id,),
                )
            return {"status": "blocked", "reason": "voice_task_cancelled_or_unassigned"}
        except asyncio.CancelledError:
            with self.store._lock, self.store.transaction():
                self.store.connection.execute(
                    "UPDATE meetings SET status='transcription_uncertain',"
                    "transcription_state='uncertain',"
                    "updated_at=CURRENT_TIMESTAMP WHERE id=? AND transcription_state='in_progress'",
                    (meeting_id,),
                )
            raise
        except Exception:
            with self.store._lock, self.store.transaction():
                self.store.connection.execute(
                    "UPDATE meetings SET status='transcription_uncertain',"
                    "transcription_state='uncertain',"
                    "updated_at=CURRENT_TIMESTAMP WHERE id=? AND transcription_state='in_progress'",
                    (meeting_id,),
                )
            return {"status": "pending_execution", "reason": "meeting_provider_outcome_uncertain"}

    def _presence_summary(self, meeting_id):
        rows = self.store.connection.execute(
            "SELECT event_id,participant_id,event_type,occurred_at,absent_seconds "
            "FROM meeting_presence_events WHERE meeting_id=? ORDER BY occurred_at,event_id",
            (meeting_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def prepare_notes(
        self,
        meeting_id,
        authenticated_actor,
        *,
        verified_facts=(),
        context_evidence=None,
    ):
        with self.store._lock:
            meeting = self._meeting(meeting_id)
            if authenticated_actor != self.notes_agent_id:
                self._actor(meeting, authenticated_actor)
            if meeting["status"] in {"cancelled", "consent_revoked"} or not meeting["transcript"]:
                raise MeetingAuthorizationError("Consented transcript artifact is unavailable")
            self._consents(meeting_id)
            notes = build_meeting_notes(
                meeting_id,
                meeting["transcript"],
                presence=self._presence_summary(meeting_id),
                verified_facts=verified_facts,
            )
            if context_evidence is not None:
                if len(encode(context_evidence)) > 32768:
                    raise ValueError("Meeting context evidence exceeds the storage limit")
                notes["context_evidence"] = context_evidence
            notes["transcript_sha256"] = meeting["transcript_sha256"]
            digest = hashlib.sha256(encode(notes).encode()).hexdigest()
            with self.store.transaction():
                current = self._meeting(meeting_id)
                self._consents(meeting_id)
                if current["transcript_sha256"] != meeting["transcript_sha256"]:
                    raise ConflictError("Meeting transcript changed while Notes were prepared")
                if current["notes"] and current["notes_sha256"] != digest:
                    raise ConflictError("Meeting already has a different Notes artifact")
                self.store.connection.execute(
                    "UPDATE meetings SET notes_json=?,notes_sha256=?,status='notes_ready',"
                    "updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (encode(notes), digest, meeting_id),
                )
                for action in notes["action_items"]:
                    existing = self.store.connection.execute(
                        "SELECT data FROM meeting_action_proposals "
                        "WHERE meeting_id=? AND proposal_id=?",
                        (meeting_id, action["proposal_id"]),
                    ).fetchone()
                    if existing and json.loads(existing[0]) != action:
                        raise ConflictError("Meeting action proposal changed")
                    self.store.connection.execute(
                        "INSERT OR IGNORE INTO meeting_action_proposals "
                        "(meeting_id,proposal_id,data) "
                        "VALUES(?,?,?)",
                        (meeting_id, action["proposal_id"], encode(action)),
                    )
                self._audit(
                    meeting_id, "notes.prepared", authenticated_actor, {"notes_sha256": digest}
                )
            return notes

    def request_notes(self, meeting_id, authenticated_actor):
        with self.store._lock, self.store.transaction():
            meeting = self._meeting(meeting_id)
            self._actor(meeting, authenticated_actor)
            if meeting["status"] in {"cancelled", "consent_revoked"} or not meeting["transcript"]:
                raise MeetingAuthorizationError("A current consented transcript is required")
            self._consents(meeting_id)
            if meeting["notes_handoff_id"]:
                return {
                    "task_id": meeting["notes_task_id"],
                    "handoff_id": meeting["notes_handoff_id"],
                }
            task_id = str(uuid5(NAMESPACE_URL, f"switchcrew:meeting:{meeting_id}:notes"))
            handoff_id = str(uuid5(NAMESPACE_URL, f"switchcrew:meeting:{meeting_id}:notes-handoff"))
            if not meeting["source"].get("room_id"):
                raise ValueError("A verified Switch room_id is required for Notes handoff delivery")
            task = self.store.create_task(
                f"Prepare evidence-linked notes for meeting {meeting_id}",
                {
                    **meeting["source"],
                    "kind": "meeting_notes",
                    "meeting_id": meeting_id,
                    "meeting_source": meeting["source"],
                },
                [
                    "Prepare concise professional minutes with transcript evidence references.",
                    "Prepare a speaker-wise summary without mapping anonymous IDs to people.",
                    "Propose evidence-linked actions; require human approval to create tasks.",
                ],
                task_id=task_id,
            )
            task = self.store.transition_task(task_id, "PLANNED", task["version"])
            task = self.store.transition_task(task_id, "IN_PROGRESS", task["version"])
            self.store.create_handoff(
                task_id,
                self.pm_id,
                self.notes_agent_id,
                f"Prepare registered meeting {meeting_id} notes. Do not infer speaker identity.",
                ["Use only the authorized transcript artifact referenced by meeting_id."],
                task["version"],
                handoff_id=handoff_id,
            )
            self.store.connection.execute(
                "UPDATE meetings SET status='notes_requested',notes_task_id=?,notes_handoff_id=?,"
                "updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (task_id, handoff_id, meeting_id),
            )
            self._audit(
                meeting_id,
                "notes.handoff_created",
                authenticated_actor,
                {"task_id": task_id, "handoff_id": handoff_id},
            )
            return {"task_id": task_id, "handoff_id": handoff_id}

    def notes_for_handoff(
        self,
        state,
        authenticated_notes_agent,
        *,
        verified_facts=(),
        context_evidence=None,
    ):
        meeting_id = self._validate_notes_handoff(state, authenticated_notes_agent)
        meeting = self._meeting(meeting_id)
        if meeting["status"] in {"cancelled", "consent_revoked"}:
            raise MeetingAuthorizationError("Meeting notes are no longer authorized")
        self._consents(meeting_id)
        handoff = self.store.get_handoff(state.get("handoff_id"))
        if handoff["status"] == "completed" and handoff["result"]:
            return handoff["result"]
        notes = self.prepare_notes(
            meeting_id,
            authenticated_notes_agent,
            verified_facts=verified_facts,
            context_evidence=context_evidence,
        )
        return self._notes_handoff_result(meeting_id, notes)

    def notes_context_for_handoff(self, state, authenticated_notes_agent):
        """Return only meeting scope metadata after validating the Notes assignment."""
        meeting_id = self._validate_notes_handoff(state, authenticated_notes_agent)
        meeting = self._meeting(meeting_id)
        if meeting["status"] in {"cancelled", "consent_revoked"}:
            raise MeetingAuthorizationError("Meeting notes are no longer authorized")
        self._consents(meeting_id)
        participants = [
            row["participant_id"]
            for row in self.store.connection.execute(
                "SELECT participant_id FROM meeting_participants WHERE meeting_id=? "
                "ORDER BY participant_id",
                (meeting_id,),
            )
        ]
        return {
            "id": meeting_id,
            "source": meeting["source"],
            "channel_id": meeting["channel_id"],
            "thread_id": meeting["thread_id"],
            "created_by": meeting["created_by"],
            "participants": participants,
        }

    def _validate_notes_handoff(self, state, authenticated_notes_agent):
        task_id, handoff_id = state.get("task_id"), state.get("handoff_id")
        with self.store._lock:
            handoff = self.store.get_handoff(handoff_id)
            task = self.store.get_task(task_id)
            if (
                authenticated_notes_agent != self.notes_agent_id
                or not handoff
                or not task
                or handoff["task_id"] != task_id
                or handoff["recipient"] != authenticated_notes_agent
                or handoff["status"] not in {"accepted", "completed"}
                or ("question" in state and state["question"] != handoff["question"])
                or (
                    "acceptance_criteria" in state
                    and state["acceptance_criteria"] != handoff["acceptance_criteria"]
                )
                or task["status"] not in {"PLANNED", "IN_PROGRESS", "COMPLETED"}
            ):
                raise MeetingAuthorizationError("Addressed Notes Switch handoff is required")
            meeting_id = task["source"].get("meeting_id")
            if not meeting_id:
                raise ValueError("Notes task does not reference a registered meeting")
            meeting = self._meeting(meeting_id)
            if meeting["notes_handoff_id"] != handoff_id or meeting["notes_task_id"] != task_id:
                raise MeetingAuthorizationError("Meeting artifact differs from addressed handoff")
            return meeting_id

    @staticmethod
    def _notes_handoff_result(meeting_id, notes):
        public_notes = {key: value for key, value in notes.items() if key != "segments"}
        return {
            "status": "completed",
            "role": "notes",
            "meeting_id": meeting_id,
            "notes_sha256": hashlib.sha256(encode(public_notes).encode()).hexdigest(),
            "notes": public_notes,
        }

    def _confirmed_actions(self, meeting_id):
        rows = self.store.connection.execute(
            "SELECT proposal_id,data,status,task_id,confirmation_data "
            "FROM meeting_action_proposals "
            "WHERE meeting_id=? ORDER BY proposal_id",
            (meeting_id,),
        ).fetchall()
        return {row["proposal_id"]: dict(row) for row in rows}

    def _normalize_action_deadline(self, value):
        if value is None:
            return None
        if not isinstance(value, str) or not value.strip():
            raise ValueError("A confirmed deadline must be an ISO date or timezone-aware datetime")
        text = value.strip()
        try:
            parsed_date = date.fromisoformat(text)
            if parsed_date.isoformat() != text:
                raise ValueError
            return parsed_date.isoformat()
        except ValueError:
            pass
        try:
            parsed_datetime = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(
                "A confirmed deadline must be an ISO date or timezone-aware datetime"
            ) from exc
        if parsed_datetime.tzinfo is None or parsed_datetime.utcoffset() is None:
            raise ValueError("A confirmed datetime deadline must include a timezone")
        return parsed_datetime.isoformat()

    def _action_dependency_complete(self, task):
        if task["status"] == "COMPLETED":
            return True
        if task["status"] != "PR_OPEN":
            return False
        workflow = self.approvals.get(task["id"])
        if not workflow or workflow.get("phase") != "pr_open":
            return False
        revision = workflow.get("revision")
        receipt = workflow.get("receipt") or {}
        verification = receipt.get("verification") or {}
        return bool(
            revision
            and receipt.get("commit_sha") == revision
            and receipt.get("draft") is True
            and receipt.get("pr_url")
            and (receipt.get("engineering_approval") or {}).get("approved") is True
            and (receipt.get("approval") or {}).get("approved") is True
            and verification.get("tested_revision") == revision
            and verification.get("reviewed_revision") == revision
        )

    def confirm_actions(
        self,
        meeting_id,
        completed_notes_handoff_id,
        confirmed_actions,
        authenticated_actor,
    ):
        if authenticated_actor not in self.human_approvers:
            raise MeetingAuthorizationError("Authorized human proposal confirmation is required")
        if not isinstance(confirmed_actions, list) or not 1 <= len(confirmed_actions) <= 50:
            raise ValueError("Confirm between 1 and 50 action proposals")
        with self.store._lock, self.store.transaction():
            meeting = self._meeting(meeting_id)
            if (
                meeting["status"] in {"cancelled", "consent_revoked"}
                or not meeting["notes"]
                or completed_notes_handoff_id != meeting["notes_handoff_id"]
            ):
                raise MeetingAuthorizationError("Completed Notes handoff is required")
            self._consents(meeting_id)
            handoff = self.store.get_handoff(completed_notes_handoff_id)
            public_notes = {
                key: value for key, value in meeting["notes"].items() if key != "segments"
            }
            expected_notes_sha256 = hashlib.sha256(encode(public_notes).encode()).hexdigest()
            if (
                not handoff
                or handoff["status"] != "completed"
                or not handoff["result"]
                or handoff["result"].get("status") != "completed"
                or handoff["result"].get("meeting_id") != meeting_id
                or handoff["result"].get("notes_sha256") != expected_notes_sha256
                or handoff["result"].get("notes") != public_notes
            ):
                raise MeetingAuthorizationError("Notes result must complete through Switch first")
            records = self._confirmed_actions(meeting_id)
            submitted = {}
            for item in confirmed_actions:
                if not isinstance(item, dict):
                    raise ValueError("Confirmed actions must be objects")
                proposal_id = item.get("proposal_id")
                if not isinstance(proposal_id, str) or proposal_id in submitted:
                    raise ValueError("Each action proposal ID must be unique")
                stored = records.get(proposal_id)
                if not stored or stored["status"] not in {"proposed", "confirmed"}:
                    raise MeetingAuthorizationError("Unknown or revoked meeting action proposal")
                proposed = json.loads(stored["data"])
                title = item.get("title", proposed["title"])
                criteria = item.get("acceptance_criteria", proposed["acceptance_criteria"])
                role = item.get("target_role")
                dependencies = item.get("depends_on", [])
                owner_id = item.get("owner_id")
                deadline = self._normalize_action_deadline(item.get("deadline"))
                if (
                    not isinstance(title, str)
                    or not title.strip()
                    or len(title) > 1000
                    or not isinstance(criteria, list)
                    or not 1 <= len(criteria) <= 20
                    or any(
                        not isinstance(c, str) or not c.strip() or len(c) > 2000 for c in criteria
                    )
                    or role not in self.targets
                    or role in _PRIVILEGED_ACTION_TARGETS
                    or not isinstance(dependencies, list)
                    or len(dependencies) > 50
                    or any(not isinstance(d, str) for d in dependencies)
                    or (
                        owner_id is not None
                        and (
                            not isinstance(owner_id, str)
                            or not self.store.connection.execute(
                                "SELECT 1 FROM meeting_participants "
                                "WHERE meeting_id=? AND participant_id=?",
                                (meeting_id, owner_id),
                            ).fetchone()
                        )
                    )
                ):
                    raise ValueError("Human-confirmed task fields are invalid")
                if not proposed["evidence_refs"]:
                    raise ValueError("Action proposal needs transcript evidence")
                confirmation = {
                    "proposal_id": proposal_id,
                    "title": title.strip(),
                    "acceptance_criteria": [c.strip() for c in criteria],
                    "target_role": role,
                    "depends_on": sorted(set(dependencies)),
                    "owner_id": owner_id,
                    "deadline": deadline,
                    "evidence_refs": proposed["evidence_refs"],
                    "assignee_speaker_id": proposed["assignee_speaker_id"],
                    "deadline_text": proposed["deadline_text"],
                }
                if (
                    stored["status"] == "confirmed"
                    and json.loads(stored["confirmation_data"]) != confirmation
                ):
                    raise ConflictError("Confirmed action changed on replay")
                submitted[proposal_id] = (confirmation, stored)
            selected_ids = set(submitted)
            for _proposal_id, (confirmation, _stored) in submitted.items():
                for dependency in confirmation["depends_on"]:
                    if dependency not in selected_ids and not (
                        dependency in records and records[dependency]["status"] == "confirmed"
                    ):
                        raise ValueError("Dependencies must reference confirmed meeting actions")
            order = self._topological_order(submitted, records)
            task_ids = {}
            for proposal_id in order:
                confirmation, record = submitted[proposal_id]
                task_id = (
                    record["task_id"]
                    if record["status"] == "confirmed"
                    else str(
                        uuid5(
                            NAMESPACE_URL, f"switchcrew:meeting:{meeting_id}:action:{proposal_id}"
                        )
                    )
                )
                task_ids[proposal_id] = task_id
                dependency_ids = [
                    task_ids.get(dep) or records[dep]["task_id"]
                    for dep in confirmation["depends_on"]
                ]
                source = {
                    **meeting["source"],
                    "kind": "confirmed_meeting_action",
                    "meeting_id": meeting_id,
                    "notes_task_id": meeting["notes_task_id"],
                    "proposal_id": proposal_id,
                    "evidence_refs": confirmation["evidence_refs"],
                    "depends_on": dependency_ids,
                    "confirmed_by": authenticated_actor,
                    "assignee_speaker_id": confirmation["assignee_speaker_id"],
                    "deadline_text": confirmation["deadline_text"],
                    "owner_id": confirmation["owner_id"],
                    "deadline": confirmation["deadline"],
                    "commitment_status": (
                        "explicitly_confirmed"
                        if confirmation["owner_id"] or confirmation["deadline"]
                        else "owner_and_deadline_unspecified"
                    ),
                }
                self.store.create_task(
                    confirmation["title"], source, confirmation["acceptance_criteria"], task_id
                )
                if record["status"] != "confirmed":
                    self.store.connection.execute(
                        "UPDATE meeting_action_proposals SET status='confirmed',task_id=?,"
                        "confirmation_data=?,confirmed_by=?,confirmed_at=? "
                        "WHERE meeting_id=? AND proposal_id=?",
                        (
                            task_id,
                            encode(confirmation),
                            authenticated_actor,
                            datetime.now(UTC).isoformat(),
                            meeting_id,
                            proposal_id,
                        ),
                    )
            self.store.connection.execute(
                "UPDATE meetings SET status='actions_confirmed',"
                "updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (meeting_id,),
            )
            self._audit(
                meeting_id,
                "actions.confirmed",
                authenticated_actor,
                {"proposal_ids": order, "task_ids": task_ids},
            )
        dispatched = self.reconcile_actions(meeting_id)
        return {
            "meeting_id": meeting_id,
            "confirmed_by": authenticated_actor,
            "tasks": task_ids,
            "dispatch": dispatched,
        }

    def reconcile_actions(self, meeting_id):
        """Dispatch confirmed actions only when every prerequisite is complete.

        Safe to call repeatedly after restart. Task creation and dispatch happen
        in a single Store transaction, and only DRAFT tasks can be dispatched.
        """
        with self.store._lock, self.store.transaction():
            meeting = self._meeting(meeting_id)
            rows = self._confirmed_actions(meeting_id)
            confirmed = {
                proposal_id: (json.loads(record["confirmation_data"]), record)
                for proposal_id, record in rows.items()
                if record["status"] == "confirmed" and record["confirmation_data"]
            }
            order = self._topological_order(confirmed, rows) if confirmed else []
            outcomes = {}
            if meeting["status"] in {"cancelled", "consent_revoked"}:
                for proposal_id in order:
                    record = rows[proposal_id]
                    task = self.store.get_task(record["task_id"])
                    if task and task["status"] == "DRAFT":
                        self.store.transition_task(task["id"], "CANCELLED", task["version"])
                    outcomes[proposal_id] = {"status": "cancelled"}
                return outcomes
            try:
                self._consents(meeting_id)
            except MeetingAuthorizationError:
                return {
                    proposal_id: {"status": "waiting", "reason": "current_consent_required"}
                    for proposal_id in order
                }
            for proposal_id in order:
                confirmation, record = confirmed[proposal_id]
                task = self.store.get_task(record["task_id"])
                if not task:
                    raise ConflictError("Confirmed meeting action task is missing")
                if task["status"] != "DRAFT":
                    state = (
                        task["status"].lower()
                        if task["status"] in {"COMPLETED", "CANCELLED", "FAILED", "BLOCKED"}
                        else "dispatched"
                    )
                    outcomes[proposal_id] = {"status": state}
                    continue
                dependency_tasks = [
                    self.store.get_task(rows[dependency]["task_id"])
                    for dependency in confirmation["depends_on"]
                ]
                if any(dep is None for dep in dependency_tasks):
                    self.store.transition_task(task["id"], "BLOCKED", task["version"])
                    outcomes[proposal_id] = {"status": "blocked", "reason": "dependency_missing"}
                    continue
                if any(
                    dep["status"] in {"FAILED", "CANCELLED", "BLOCKED"} for dep in dependency_tasks
                ):
                    self.store.transition_task(task["id"], "BLOCKED", task["version"])
                    outcomes[proposal_id] = {
                        "status": "blocked",
                        "reason": "dependency_failed_or_cancelled",
                    }
                    continue
                if any(not self._action_dependency_complete(dep) for dep in dependency_tasks):
                    outcomes[proposal_id] = {"status": "waiting", "reason": "dependencies_pending"}
                    continue
                # Revalidate immediately before creating any handoff or approval.
                current_meeting = self._meeting(meeting_id)
                if current_meeting["status"] in {"cancelled", "consent_revoked"}:
                    self.store.transition_task(task["id"], "CANCELLED", task["version"])
                    outcomes[proposal_id] = {"status": "cancelled"}
                    continue
                self._consents(meeting_id)
                if confirmation["target_role"] == "engineering":
                    self._request_engineering_approval(task["id"], confirmation["target_role"])
                else:
                    task = self.store.transition_task(task["id"], "PLANNED", task["version"])
                    task = self.store.transition_task(task["id"], "IN_PROGRESS", task["version"])
                    handoff_id = str(uuid5(NAMESPACE_URL, f"{task['id']}:meeting-confirmed"))
                    dependency_ids = [dep["id"] for dep in dependency_tasks]
                    question = (
                        f"Confirmed meeting action: {confirmation['title']}\n"
                        "Transcript evidence references: "
                        f"{', '.join(confirmation['evidence_refs'])}\n"
                        "Dependency task IDs: "
                        f"{', '.join(dependency_ids) if dependency_ids else 'none'}"
                    )
                    self.store.create_handoff(
                        task["id"],
                        self.pm_id,
                        self.targets[confirmation["target_role"]],
                        question,
                        confirmation["acceptance_criteria"],
                        task["version"],
                        handoff_id=handoff_id,
                    )
                outcomes[proposal_id] = {"status": "dispatched"}
            self._audit(meeting_id, "actions.reconciled", self.pm_id, outcomes)
            return outcomes

    def _request_engineering_approval(self, task_id, role):
        route = self.engineering_route
        if not all(
            route.get(key) for key in ("qa", "reviewer", "release", "job_key", "repository", "base")
        ):
            raise ValueError("A fully configured approval-gated Engineering route is required")
        self.approvals.request(
            task_id,
            self.pm_id,
            self.targets[role],
            route["qa"],
            route["release"],
            route["job_key"],
            route["repository"],
            route["base"],
            reviewer=route["reviewer"],
            max_corrections=route.get("max_corrections", 2),
        )

    @staticmethod
    def _topological_order(submitted, records):
        result, visiting, visited = [], set(), set()

        def visit(proposal_id):
            if proposal_id in visiting:
                raise ValueError("Meeting action dependencies contain a cycle")
            if proposal_id in visited:
                return
            visiting.add(proposal_id)
            confirmation = submitted.get(proposal_id, (None,))[0]
            if confirmation:
                for dependency in confirmation["depends_on"]:
                    if dependency in submitted:
                        visit(dependency)
                    elif dependency not in records or records[dependency]["status"] != "confirmed":
                        raise ValueError("Meeting action dependency is not confirmed")
            visiting.remove(proposal_id)
            visited.add(proposal_id)
            result.append(proposal_id)

        for proposal_id in submitted:
            visit(proposal_id)
        return result

    def live_capture_status(self):
        return {
            "status": "blocked",
            "capability": "live_discord_voice_capture",
            "reason": "No authenticated Discord voice transport or verified DAVE receiver.",
        }


class MeetingVoiceExecutor:
    """Run registered recording jobs only from the addressed Voice Switch identity."""

    def __init__(self, meeting_service, *, agent_id):
        if not isinstance(agent_id, str) or not agent_id:
            raise ValueError("A configured Voice Switch identity is required")
        self.meeting_service, self.agent_id = meeting_service, agent_id

    async def __call__(self, state):
        try:
            return await self.meeting_service.transcribe_for_handoff(state, self.agent_id)
        except PermissionError:
            return {"status": "blocked", "reason": "addressed_voice_handoff_required"}
        except (ValueError, KeyError, TypeError):
            return {"status": "blocked", "reason": "registered_recording_job_required"}
