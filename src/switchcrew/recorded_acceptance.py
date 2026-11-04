"""Read-only acceptance of consented recorded-meeting artifacts in SQLite.

No transcript bodies, consent tokens, participant names or raw audio are printed.
This checks stored invariants; external service authenticity still needs live proof.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from .store import encode

_AUDIT_EVENTS = {"meeting.created", "consent.granted", "transcription.completed", "notes.prepared"}


def _digest(value):
    return hashlib.sha256(encode(value).encode()).hexdigest()


def inspect_recorded_meeting(database: str | Path, meeting_id: str) -> dict:
    if not isinstance(meeting_id, str) or not 1 <= len(meeting_id) <= 200:
        raise ValueError("A bounded meeting ID is required")
    database = Path(database).resolve(strict=True)
    if not database.is_file():
        raise ValueError("An existing SQLite file is required")
    checks: dict[str, bool] = {}
    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        record = db.execute("SELECT * FROM meetings WHERE id=?", (meeting_id,)).fetchone()
        if record is None:
            raise ValueError("Meeting is not registered")
        record = dict(record)
        participants = db.execute(
            "SELECT consent_evidence,consent_expires_at,consent_granted_at,consent_revoked_at "
            "FROM meeting_participants WHERE meeting_id=?",
            (meeting_id,),
        ).fetchall()
        now = datetime.now(UTC)
        consents = []
        for participant in participants:
            try:
                expiry = datetime.fromisoformat(participant["consent_expires_at"])
                consents.append(
                    bool(participant["consent_evidence"] and participant["consent_granted_at"])
                    and not participant["consent_revoked_at"]
                    and expiry.tzinfo is not None
                    and expiry > now
                )
            except (TypeError, ValueError):
                consents.append(False)
        checks["all_participants_consented"] = bool(consents) and all(consents)
        checks["meeting_not_revoked"] = record["status"] not in {"cancelled", "consent_revoked"}
        job = db.execute(
            "SELECT status,input_sha256,transcript_sha256 FROM meeting_recording_jobs "
            "WHERE meeting_id=?",
            (meeting_id,),
        ).fetchone()
        checks["registered_voice_handoff"] = (
            job is not None
            and job["status"] == "completed"
            and job["input_sha256"] == record["input_sha256"]
            and job["transcript_sha256"] == record["transcript_sha256"]
        )
        try:
            transcript = json.loads(record["transcript_json"] or "null")
            notes = json.loads(record["notes_json"] or "null")
        except (TypeError, ValueError):
            transcript, notes = None, None
        provenance = (
            transcript.get("recording_provenance", {}) if isinstance(transcript, dict) else {}
        )
        checks["recorded_source_pinned"] = bool(
            isinstance(provenance, dict)
            and provenance.get("capture_mode") == "recorded_file"
            and record["input_sha256"]
            and provenance.get("input_sha256") == record["input_sha256"]
        )
        checks["transcript_digest_valid"] = bool(
            isinstance(transcript, dict) and record["transcript_sha256"] == _digest(transcript)
        )
        checks["notes_digest_valid"] = bool(
            isinstance(notes, dict)
            and notes.get("transcript_sha256") == record["transcript_sha256"]
            and record["notes_sha256"] == _digest(notes)
        )
        audit = {
            row[0]
            for row in db.execute(
                "SELECT DISTINCT event_type FROM meeting_audit_events WHERE meeting_id=?",
                (meeting_id,),
            )
        }
        checks["audit_history_present"] = _AUDIT_EVENTS <= audit
        checks["notes_assignment_registered"] = bool(
            record["notes_task_id"]
            and record["notes_handoff_id"]
            and db.execute(
                "SELECT 1 FROM handoffs WHERE id=? AND task_id=?",
                (record["notes_handoff_id"], record["notes_task_id"]),
            ).fetchone()
        )
        presence_count = db.execute(
            "SELECT COUNT(*) FROM meeting_presence_events WHERE meeting_id=?", (meeting_id,)
        ).fetchone()[0]
    pending = [name for name, passed in checks.items() if not passed]
    return {
        "meeting_id": meeting_id,
        "status": "recorded_artifacts_valid" if not pending else "incomplete",
        "checks": checks,
        "pending": pending,
        "presence_event_count": presence_count,
        "live_discord_capture": "not_verified",
        "interactive_clarifications": "not_verified",
        "external_provider_authenticity": "not_verified",
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Inspect consented recorded-meeting evidence")
    parser.add_argument("--database", required=True)
    parser.add_argument("--meeting-id", required=True)
    args = parser.parse_args(argv)
    report = inspect_recorded_meeting(args.database, args.meeting_id)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["status"] == "recorded_artifacts_valid" else 2


if __name__ == "__main__":
    raise SystemExit(main())
