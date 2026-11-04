import asyncio
import json
import uuid
import wave
from datetime import UTC, datetime, timedelta

import pytest

from switchcrew.meetings import MeetingAuthorizationError, MeetingService, MeetingVoiceExecutor
from switchcrew.notes import NotesExecutor, build_meeting_notes
from switchcrew.store import ConflictError, Store


def wav(path, seconds=2):
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b"\x00\x00" * 16000 * seconds)


def utterance(text, start, speaker):
    return {
        "text": text,
        "start": start,
        "end": start + 1.0,
        "type": "word",
        "speaker_id": speaker,
        "speaker_attribution": "provider_anonymous_id" if speaker else "unattributed",
        "logprob": -0.1,
    }


class FakeProvider:
    def __init__(self, *, wait=None, failure=False):
        self.wait, self.failure, self.calls = wait, failure, 0

    def spec(self):
        return {"provider": "fixture-stt", "model": "meeting-fixture-v1"}

    async def transcribe_meeting(self, audio):
        self.calls += 1
        if self.wait:
            self.wait[0].set()
            await self.wait[1].wait()
        if self.failure:
            raise TimeoutError("private-provider-error")
        return {
            "text": (
                "I will update API by Friday. We decided the API is ready. "
                "Could someone review the docs? Deployment is blocked by auth."
            ),
            "words": [
                utterance("I will update API by Friday.", 0.1, "speaker_0"),
                utterance("We decided the API is ready.", 3.0, "speaker_1"),
                utterance("Could someone review the docs?", 6.0, None),
                utterance("Deployment is blocked by auth.", 9.0, "speaker_1"),
            ],
        }


def service(tmp_path, *, provider=None, targets=None, route=None):
    db = Store(tmp_path / "meetings.sqlite3")
    upload_root = tmp_path / "recordings"
    upload_root.mkdir(exist_ok=True)
    wav(upload_root / "meeting.wav")
    meeting_service = MeetingService(
        db,
        provider,
        upload_root,
        pm_id="pm-agent",
        notes_agent_id="notes-agent",
        specialist_targets=targets or {"research": "research-agent"},
        human_approvers=["alice"],
        engineering_route=route,
    )
    return db, meeting_service, upload_root


def make_meeting(meetings, *, participants=("alice", "bob")):
    return meetings.create_meeting(
        "meeting-1",
        "discord-channel",
        "discord-thread",
        "alice",
        list(participants),
        source={
            "room_id": "switch-room",
            "guild_id": "guild-1",
            "channel_id": "discord-channel",
            "message_id": "discord-message",
        },
    )


def consent(meetings, participants=("alice", "bob")):
    expires = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    for participant in participants:
        meetings.grant_consent(
            "meeting-1",
            participant,
            participant,
            evidence_id=f"consent-{participant}",
            expires_at=expires,
            disclosure_acknowledged=True,
        )


async def complete_notes_handoff(db, meetings):
    await meetings.transcribe_recording("meeting-1", "meeting.wav", "alice")
    handoff = meetings.request_notes("meeting-1", "alice")
    db.accept_handoff(handoff["handoff_id"], "notes-agent")
    result = await NotesExecutor(meetings, agent_id="notes-agent")(
        {"task_id": handoff["task_id"], "handoff_id": handoff["handoff_id"]}
    )
    db.complete_handoff(handoff["handoff_id"], "notes-agent", result)
    return handoff, result


@pytest.mark.asyncio
async def test_transcription_is_queued_over_voice_switch_and_result_contains_no_transcript(
    tmp_path,
):
    db, meetings, _ = service(tmp_path, provider=FakeProvider())
    make_meeting(meetings)
    consent(meetings)
    job = meetings.request_transcription(
        "meeting-1", "meeting.wav", "alice", voice_agent_id="voice-agent"
    )
    assert job["status"] == "queued"
    assert job["recording_job_id"]
    assert job["task_id"]
    assert job["handoff_id"]
    provider = meetings.provider
    assert provider.calls == 0
    task = db.get_task(job["task_id"])
    assert task["source"]["kind"] == "meeting_transcription"
    assert task["source"]["recording_job_id"] == job["recording_job_id"]
    assert "filename" not in task["source"]
    handoff = db.get_handoff(job["handoff_id"])
    assert handoff["recipient"] == "voice-agent"
    assert "meeting.wav" not in handoff["question"]
    replay = meetings.request_transcription(
        "meeting-1", "meeting.wav", "alice", voice_agent_id="voice-agent"
    )
    assert replay["recording_job_id"] == job["recording_job_id"]
    assert provider.calls == 0
    assert (await meetings.transcribe_recording("meeting-1", "meeting.wav", "alice"))[
        "reason"
    ] == "voice_switch_handoff_required"
    db.accept_handoff(job["handoff_id"], "voice-agent")

    executor = MeetingVoiceExecutor(meetings, agent_id="voice-agent")
    state = {"task_id": job["task_id"], "handoff_id": job["handoff_id"]}
    result = await executor(state)
    assert result["status"] == "completed"
    assert result["role"] == "voice"
    assert result["transcript_sha256"]
    assert result["recording_provenance"]["capture_mode"] == "recorded_file"
    assert "transcript" not in result and "words" not in result
    assert provider.calls == 1
    replayed_result = await executor(state)
    assert replayed_result == result
    assert provider.calls == 1
    persisted = db.connection.execute(
        "SELECT status,result_json FROM meeting_recording_jobs WHERE id=?",
        (job["recording_job_id"],),
    ).fetchone()
    assert persisted["status"] == "completed"
    assert "I will update API" not in persisted["result_json"]
    meetings.revoke_consent("meeting-1", "bob", "bob")
    revoked_replay = await executor(state)
    assert revoked_replay["status"] == "blocked"
    assert "transcript_sha256" not in revoked_replay
    persisted = db.connection.execute(
        "SELECT status,result_json FROM meeting_recording_jobs WHERE id=?",
        (job["recording_job_id"],),
    ).fetchone()
    assert persisted["status"] == "revoked"
    assert persisted["result_json"] is None
    db.close()


@pytest.mark.asyncio
async def test_voice_switch_job_survives_api_restart_and_runs_in_provider_process(tmp_path):
    api_store, api_meetings, input_root = service(tmp_path, provider=None)
    make_meeting(api_meetings)
    consent(api_meetings)
    job = api_meetings.request_transcription(
        "meeting-1", "meeting.wav", "alice", voice_agent_id="voice-agent"
    )
    api_store.accept_handoff(job["handoff_id"], "voice-agent")
    api_store.close()

    voice_store = Store(tmp_path / "meetings.sqlite3")
    provider = FakeProvider()
    voice_meetings = MeetingService(
        voice_store,
        provider,
        input_root,
        pm_id="pm-agent",
        notes_agent_id="notes-agent",
        specialist_targets={"research": "research-agent"},
        human_approvers=["alice"],
    )
    result = await MeetingVoiceExecutor(voice_meetings, agent_id="voice-agent")(
        {"task_id": job["task_id"], "handoff_id": job["handoff_id"]}
    )
    assert result["status"] == "completed"
    assert provider.calls == 1
    voice_store.close()


@pytest.mark.asyncio
async def test_missing_voice_provider_is_blocked_without_fabricated_result(tmp_path):
    db, meetings, _ = service(tmp_path, provider=None)
    make_meeting(meetings)
    consent(meetings)
    job = meetings.request_transcription(
        "meeting-1", "meeting.wav", "alice", voice_agent_id="voice-agent"
    )
    db.accept_handoff(job["handoff_id"], "voice-agent")
    result = await MeetingVoiceExecutor(meetings, agent_id="voice-agent")(
        {"task_id": job["task_id"], "handoff_id": job["handoff_id"]}
    )
    assert result["status"] == "blocked_configuration"
    assert "transcript" not in result
    stored = db.connection.execute(
        "SELECT status,transcript_sha256 FROM meeting_recording_jobs WHERE id=?",
        (job["recording_job_id"],),
    ).fetchone()
    assert tuple(stored) == ("blocked", None)
    db.close()


@pytest.mark.asyncio
async def test_production_recording_manifest_binds_file_meeting_owner_and_roster(tmp_path):
    db, meetings, root = service(tmp_path, provider=FakeProvider())
    make_meeting(meetings)
    consent(meetings)

    def strict_service(manifest):
        return MeetingService(
            db,
            FakeProvider(),
            root,
            pm_id="pm-agent",
            notes_agent_id="notes-agent",
            specialist_targets={"research": "research-agent"},
            human_approvers=["reviewer"],
            recording_manifest=manifest,
            require_recording_authorization=True,
        )

    base = {"meeting_id": "meeting-1", "owner_id": "alice", "participants": ["alice", "bob"]}
    unauthorized = [
        ({"meeting.wav": {**base, "meeting_id": "other-meeting"}}, "meeting.wav", "alice"),
        ({"meeting.wav": {**base, "owner_id": "bob"}}, "meeting.wav", "alice"),
        ({"meeting.wav": {**base, "participants": ["alice"]}}, "meeting.wav", "alice"),
        ({}, "meeting.wav", "alice"),
    ]
    (root / "unlisted.wav").write_bytes((root / "meeting.wav").read_bytes())
    unauthorized.append(({"meeting.wav": base}, "unlisted.wav", "alice"))
    for manifest, filename, actor in unauthorized:
        strict = strict_service(manifest)
        with pytest.raises(MeetingAuthorizationError):
            strict.request_transcription("meeting-1", filename, actor, voice_agent_id="voice-agent")
        assert strict.provider.calls == 0
        assert await strict.transcribe_recording("meeting-1", filename, actor) == {
            "status": "blocked",
            "reason": "voice_switch_handoff_required",
        }

    strict = strict_service({"meeting.wav": base})
    request = strict.request_transcription(
        "meeting-1", "meeting.wav", "alice", voice_agent_id="voice-agent"
    )
    assert request["status"] == "queued"
    assert strict.provider.calls == 0
    assert await strict.transcribe_recording("meeting-1", "meeting.wav", "alice") == {
        "status": "blocked",
        "reason": "voice_switch_handoff_required",
    }
    assert strict.provider.calls == 0
    db.close()


@pytest.mark.asyncio
async def test_recording_job_requires_consent_and_authenticated_addressed_voice_handoff(tmp_path):
    db, meetings, _ = service(tmp_path, provider=FakeProvider())
    make_meeting(meetings)
    with pytest.raises(MeetingAuthorizationError):
        meetings.request_transcription(
            "meeting-1", "meeting.wav", "mallory", voice_agent_id="voice-agent"
        )
    with pytest.raises(MeetingAuthorizationError):
        meetings.request_transcription(
            "meeting-1", "meeting.wav", "alice", voice_agent_id="voice-agent"
        )
    consent(meetings)
    job = meetings.request_transcription(
        "meeting-1", "meeting.wav", "alice", voice_agent_id="voice-agent"
    )
    executor = MeetingVoiceExecutor(meetings, agent_id="voice-agent")
    wrong = await executor({"task_id": job["task_id"], "handoff_id": job["handoff_id"]})
    assert wrong["status"] == "blocked"
    db.accept_handoff(job["handoff_id"], "voice-agent")
    wrong_identity = await MeetingVoiceExecutor(meetings, agent_id="other-agent")(
        {"task_id": job["task_id"], "handoff_id": job["handoff_id"]}
    )
    assert wrong_identity["status"] == "blocked"
    assert meetings.provider.calls == 0
    db.close()


@pytest.mark.asyncio
async def test_voice_worker_rejects_changed_file_and_cancellation_before_upload(tmp_path):
    db, meetings, root = service(tmp_path, provider=FakeProvider())
    make_meeting(meetings)
    consent(meetings)
    job = meetings.request_transcription(
        "meeting-1", "meeting.wav", "alice", voice_agent_id="voice-agent"
    )
    db.accept_handoff(job["handoff_id"], "voice-agent")
    wav(root / "meeting.wav", seconds=3)
    changed = await MeetingVoiceExecutor(meetings, agent_id="voice-agent")(
        {"task_id": job["task_id"], "handoff_id": job["handoff_id"]}
    )
    assert changed["status"] == "blocked"
    assert changed["reason"] == "recording_changed_after_request"
    assert meetings.provider.calls == 0

    cancelled_root = tmp_path / "cancelled"
    cancelled_root.mkdir()
    db, meetings, _ = service(cancelled_root, provider=FakeProvider())
    make_meeting(meetings)
    consent(meetings)
    job = meetings.request_transcription(
        "meeting-1", "meeting.wav", "alice", voice_agent_id="voice-agent"
    )
    meetings.cancel_meeting("meeting-1", "alice")
    cancelled = await MeetingVoiceExecutor(meetings, agent_id="voice-agent")(
        {"task_id": job["task_id"], "handoff_id": job["handoff_id"]}
    )
    assert cancelled["status"] == "blocked"
    assert meetings.provider.calls == 0
    db.close()


@pytest.mark.asyncio
async def test_revoke_during_voice_upload_discards_transcript_and_keeps_job_revoked(tmp_path):
    started, release = asyncio.Event(), asyncio.Event()
    provider = FakeProvider(wait=(started, release))
    db, meetings, _ = service(tmp_path, provider=provider)
    make_meeting(meetings)
    consent(meetings)
    job = meetings.request_transcription(
        "meeting-1", "meeting.wav", "alice", voice_agent_id="voice-agent"
    )
    db.accept_handoff(job["handoff_id"], "voice-agent")
    executor = MeetingVoiceExecutor(meetings, agent_id="voice-agent")
    task = asyncio.create_task(
        executor({"task_id": job["task_id"], "handoff_id": job["handoff_id"]})
    )
    await started.wait()
    meetings.revoke_consent("meeting-1", "bob", "bob")
    release.set()
    result = await task
    assert result["status"] == "blocked"
    assert "transcript" not in result
    assert meetings._meeting("meeting-1")["transcript"] is None
    status = db.connection.execute(
        "SELECT status FROM meeting_recording_jobs WHERE id=?", (job["recording_job_id"],)
    ).fetchone()[0]
    assert status == "revoked"
    db.close()


@pytest.mark.asyncio
async def test_task_cancel_during_voice_upload_discards_transcript(tmp_path):
    started, release = asyncio.Event(), asyncio.Event()
    provider = FakeProvider(wait=(started, release))
    db, meetings, _ = service(tmp_path, provider=provider)
    make_meeting(meetings)
    consent(meetings)
    job = meetings.request_transcription(
        "meeting-1", "meeting.wav", "alice", voice_agent_id="voice-agent"
    )
    db.accept_handoff(job["handoff_id"], "voice-agent")
    executor = MeetingVoiceExecutor(meetings, agent_id="voice-agent")
    pending = asyncio.create_task(
        executor({"task_id": job["task_id"], "handoff_id": job["handoff_id"]})
    )
    await started.wait()
    current = db.get_task(job["task_id"])
    db.transition_task(job["task_id"], "CANCELLED", current["version"])
    release.set()
    result = await pending
    assert result["status"] == "blocked"
    assert meetings._meeting("meeting-1")["transcript"] is None
    job_status = db.connection.execute(
        "SELECT status FROM meeting_recording_jobs WHERE id=?", (job["recording_job_id"],)
    ).fetchone()[0]
    assert job_status == "blocked"
    db.close()


@pytest.mark.asyncio
async def test_recorded_meeting_consent_transcript_notes_switch_and_confirmed_handoff(tmp_path):
    db, meetings, _ = service(tmp_path, provider=FakeProvider())
    make_meeting(meetings)
    with pytest.raises(MeetingAuthorizationError):
        meetings.grant_consent(
            "meeting-1",
            "bob",
            "alice",
            evidence_id="spoofed-consent",
            expires_at=(datetime.now(UTC) + timedelta(days=1)).isoformat(),
            disclosure_acknowledged=True,
        )
    with pytest.raises(MeetingAuthorizationError):
        meetings.grant_consent(
            "meeting-1",
            "alice",
            "alice",
            evidence_id="no-disclosure",
            expires_at=(datetime.now(UTC) + timedelta(days=1)).isoformat(),
            disclosure_acknowledged=False,
        )
    consent(meetings)

    transcript = await meetings.transcribe_recording("meeting-1", "meeting.wav", "alice")
    assert transcript["status"] == "completed"
    assert transcript["transcript"]["recording_provenance"]["capture_mode"] == "recorded_file"
    assert transcript["transcript"]["words"][0]["speaker_id"] == "speaker_0"
    assert transcript["transcript"]["speaker_identity_policy"].startswith("Provider speaker IDs")

    handoff = meetings.request_notes("meeting-1", "alice")
    assert meetings.request_notes("meeting-1", "alice") == handoff
    notes_handoff = db.get_handoff(handoff["handoff_id"])
    assert notes_handoff["recipient"] == "notes-agent"
    assert "meeting_id" not in notes_handoff["question"]
    with pytest.raises(ValueError):
        meetings.confirm_actions("meeting-1", handoff["handoff_id"], [], "alice")

    db.accept_handoff(handoff["handoff_id"], "notes-agent")
    executor = NotesExecutor(meetings, agent_id="notes-agent")
    notes_result = await executor(
        {
            "task_id": handoff["task_id"],
            "handoff_id": handoff["handoff_id"],
        }
    )
    assert notes_result["status"] == "completed"
    assert notes_result["notes"]["minutes"]["decisions"][0]["evidence_refs"]
    assert notes_result["notes"]["speaker_wise_summary"]["speakers"][0]["speaker_id"] == (
        "speaker_0"
    )
    assert notes_result["notes"]["action_items"][0]["assignee_identity"] is None
    assert notes_result["notes"]["action_items"][0]["evidence_refs"]
    assert "private-provider-error" not in json.dumps(notes_result)
    db.complete_handoff(handoff["handoff_id"], "notes-agent", notes_result)

    proposal = notes_result["notes"]["action_items"][0]
    created = meetings.confirm_actions(
        "meeting-1",
        handoff["handoff_id"],
        [
            {
                "proposal_id": proposal["proposal_id"],
                "title": "Update API behavior",
                "acceptance_criteria": ["Tests verify the agreed API behavior"],
                "target_role": "research",
                "depends_on": [],
            }
        ],
        "alice",
    )
    task_id = created["tasks"][proposal["proposal_id"]]
    task = db.get_task(task_id)
    task_handoff = db.get_handoff(
        str(uuid.uuid5(uuid.NAMESPACE_URL, f"{task_id}:meeting-confirmed"))
    )
    assert task["status"] == "IN_PROGRESS"
    assert task["source"]["meeting_id"] == "meeting-1"
    assert task["source"]["evidence_refs"] == proposal["evidence_refs"]
    assert task_handoff["recipient"] == "research-agent"
    assert task_handoff["question"].find("seg-00001") >= 0
    assert (
        meetings.confirm_actions(
            "meeting-1",
            handoff["handoff_id"],
            [
                {
                    "proposal_id": proposal["proposal_id"],
                    "title": "Update API behavior",
                    "acceptance_criteria": ["Tests verify the agreed API behavior"],
                    "target_role": "research",
                    "depends_on": [],
                }
            ],
            "alice",
        )
        == created
    )
    meetings.revoke_consent("meeting-1", "bob", "bob")
    revoked_replay = await executor(
        {"task_id": handoff["task_id"], "handoff_id": handoff["handoff_id"]}
    )
    assert revoked_replay["status"] == "blocked"
    assert meetings.live_capture_status()["status"] == "blocked"
    db.close()


@pytest.mark.asyncio
async def test_confirmed_action_dependencies_wait_and_reconcile_once_after_restart(tmp_path):
    db, meetings, input_root = service(tmp_path, provider=FakeProvider())
    make_meeting(meetings)
    consent(meetings)
    await meetings.transcribe_recording("meeting-1", "meeting.wav", "alice")
    notes_handoff = meetings.request_notes("meeting-1", "alice")
    db.accept_handoff(notes_handoff["handoff_id"], "notes-agent")
    notes_result = await NotesExecutor(meetings, agent_id="notes-agent")(
        {"task_id": notes_handoff["task_id"], "handoff_id": notes_handoff["handoff_id"]}
    )
    db.complete_handoff(notes_handoff["handoff_id"], "notes-agent", notes_result)
    first = notes_result["notes"]["action_items"][0]
    second = {**first, "proposal_id": "followup-proposal", "title": "Follow up on API"}
    db.connection.execute(
        "INSERT INTO meeting_action_proposals(meeting_id,proposal_id,data) VALUES(?,?,?)",
        ("meeting-1", second["proposal_id"], json.dumps(second, sort_keys=True)),
    )
    confirmed = meetings.confirm_actions(
        "meeting-1",
        notes_handoff["handoff_id"],
        [
            {
                "proposal_id": first["proposal_id"],
                "title": "Update API behavior",
                "acceptance_criteria": ["Tests verify API behavior"],
                "target_role": "research",
                "depends_on": [],
            },
            {
                "proposal_id": second["proposal_id"],
                "title": "Follow up on API",
                "acceptance_criteria": ["Publish follow-up findings"],
                "target_role": "research",
                "depends_on": [first["proposal_id"]],
                "owner_id": "bob",
                "deadline": "2026-11-10T17:00:00+05:30",
            },
        ],
        "alice",
    )
    first_task_id = confirmed["tasks"][first["proposal_id"]]
    second_task_id = confirmed["tasks"][second["proposal_id"]]
    assert db.get_task(first_task_id)["status"] == "IN_PROGRESS"
    waiting = db.get_task(second_task_id)
    assert waiting["status"] == "DRAFT"
    assert waiting["source"]["owner_id"] == "bob"
    assert waiting["source"]["deadline"] == "2026-11-10T17:00:00+05:30"
    assert waiting["source"]["assignee_speaker_id"] == second["assignee_speaker_id"]
    assert (
        db.connection.execute(
            "SELECT COUNT(*) FROM handoffs WHERE task_id=?", (second_task_id,)
        ).fetchone()[0]
        == 0
    )

    assert meetings.reconcile_actions("meeting-1")[second["proposal_id"]]["status"] == "waiting"
    db.connection.execute("UPDATE tasks SET status='PR_OPEN' WHERE id=?", (first_task_id,))
    workflow = {
        "phase": "pr_open",
        "revision": "revision-123",
        "receipt": {"commit_sha": "wrong-revision"},
    }
    meetings.approvals._save(first_task_id, workflow)
    assert meetings.reconcile_actions("meeting-1")[second["proposal_id"]]["status"] == "waiting"
    workflow["receipt"] = {
        "commit_sha": "revision-123",
        "draft": True,
        "pr_url": "https://github.com/acme/repo/pull/1",
        "engineering_approval": {"approved": True},
        "approval": {"approved": True},
        "verification": {
            "tested_revision": "revision-123",
            "reviewed_revision": "revision-123",
        },
    }
    meetings.approvals._save(first_task_id, workflow)
    db.close()

    reopened = Store(tmp_path / "meetings.sqlite3")
    restarted = MeetingService(
        reopened,
        None,
        input_root,
        pm_id="pm-agent",
        notes_agent_id="notes-agent",
        specialist_targets={"research": "research-agent"},
        human_approvers=["alice"],
    )
    result = restarted.reconcile_actions("meeting-1")
    assert result[second["proposal_id"]]["status"] == "dispatched"
    assert reopened.get_task(second_task_id)["status"] == "IN_PROGRESS"
    assert (
        reopened.connection.execute(
            "SELECT COUNT(*) FROM handoffs WHERE task_id=?", (second_task_id,)
        ).fetchone()[0]
        == 1
    )
    replay = restarted.reconcile_actions("meeting-1")
    assert replay[second["proposal_id"]]["status"] == "dispatched"
    assert (
        reopened.connection.execute(
            "SELECT COUNT(*) FROM handoffs WHERE task_id=?", (second_task_id,)
        ).fetchone()[0]
        == 1
    )
    reopened.close()


@pytest.mark.asyncio
async def test_confirmed_action_rejects_unverified_owner_and_ambiguous_deadline(tmp_path):
    db, meetings, _ = service(tmp_path, provider=FakeProvider())
    make_meeting(meetings)
    consent(meetings)
    await meetings.transcribe_recording("meeting-1", "meeting.wav", "alice")
    notes_handoff = meetings.request_notes("meeting-1", "alice")
    db.accept_handoff(notes_handoff["handoff_id"], "notes-agent")
    notes_result = await NotesExecutor(meetings, agent_id="notes-agent")(
        {"task_id": notes_handoff["task_id"], "handoff_id": notes_handoff["handoff_id"]}
    )
    db.complete_handoff(notes_handoff["handoff_id"], "notes-agent", notes_result)
    proposal = notes_result["notes"]["action_items"][0]
    base_action = {
        "proposal_id": proposal["proposal_id"],
        "title": "Resolve API behavior",
        "acceptance_criteria": ["Record a verified decision"],
        "target_role": "research",
        "depends_on": [],
    }
    with pytest.raises(ValueError):
        meetings.confirm_actions(
            "meeting-1",
            notes_handoff["handoff_id"],
            [{**base_action, "owner_id": "mallory"}],
            "alice",
        )
    with pytest.raises(ValueError):
        meetings.confirm_actions(
            "meeting-1",
            notes_handoff["handoff_id"],
            [{**base_action, "deadline": "next Friday"}],
            "alice",
        )
    with pytest.raises(ValueError):
        meetings.confirm_actions(
            "meeting-1",
            notes_handoff["handoff_id"],
            [{**base_action, "deadline": "2026-11-10T17:00:00"}],
            "alice",
        )
    confirmed = meetings.confirm_actions(
        "meeting-1", notes_handoff["handoff_id"], [base_action], "alice"
    )
    task = db.get_task(confirmed["tasks"][proposal["proposal_id"]])
    assert task["source"]["owner_id"] is None
    assert task["source"]["deadline"] is None
    assert task["source"]["commitment_status"] == "owner_and_deadline_unspecified"
    meetings.targets.update(
        {
            "qa": "qa-agent",
            "reviewer": "review-agent",
            "release": "release-agent",
            "publisher": "publisher-agent",
            "security": "security-agent",
        }
    )
    for privileged_role in ("qa", "reviewer", "release", "publisher", "security"):
        with pytest.raises(ValueError):
            meetings.confirm_actions(
                "meeting-1",
                notes_handoff["handoff_id"],
                [{**base_action, "target_role": privileged_role}],
                "alice",
            )
    db.close()


@pytest.mark.asyncio
async def test_revoked_consent_cancels_waiting_actions_without_dispatch(tmp_path):
    db, meetings, _ = service(tmp_path, provider=FakeProvider())
    make_meeting(meetings)
    consent(meetings)
    notes_handoff, notes_result = await complete_notes_handoff(db, meetings)
    assert notes_result["notes"]["context_evidence"]["warnings"]
    first = notes_result["notes"]["action_items"][0]
    second = {**first, "proposal_id": "revoked-followup"}
    db.connection.execute(
        "INSERT INTO meeting_action_proposals(meeting_id,proposal_id,data) VALUES(?,?,?)",
        ("meeting-1", second["proposal_id"], json.dumps(second, sort_keys=True)),
    )
    confirmed = meetings.confirm_actions(
        "meeting-1",
        notes_handoff["handoff_id"],
        [
            {
                "proposal_id": first["proposal_id"],
                "target_role": "research",
                "depends_on": [],
            },
            {
                "proposal_id": second["proposal_id"],
                "target_role": "research",
                "depends_on": [first["proposal_id"]],
            },
        ],
        "alice",
    )
    dependent_id = confirmed["tasks"][second["proposal_id"]]
    assert db.get_task(dependent_id)["status"] == "DRAFT"
    meetings.revoke_consent("meeting-1", "bob", "bob")
    assert db.get_task(dependent_id)["status"] == "CANCELLED"
    assert meetings.reconcile_actions("meeting-1") == {}
    assert (
        db.connection.execute(
            "SELECT COUNT(*) FROM handoffs WHERE task_id=?", (dependent_id,)
        ).fetchone()[0]
        == 0
    )
    db.close()


@pytest.mark.asyncio
async def test_engineering_meeting_action_enters_human_approval_workflow(tmp_path):
    db, meetings, _ = service(tmp_path, provider=FakeProvider())
    make_meeting(meetings)
    consent(meetings)
    notes_handoff, notes_result = await complete_notes_handoff(db, meetings)
    proposal = notes_result["notes"]["action_items"][0]
    meetings.targets["engineering"] = "engineering-agent"
    meetings.engineering_route = {
        "qa": "qa-agent",
        "reviewer": "reviewer-agent",
        "release": "release-agent",
        "job_key": "api-job",
        "repository": "acme/repository",
        "base": "main",
    }
    result = meetings.confirm_actions(
        "meeting-1",
        notes_handoff["handoff_id"],
        [
            {
                "proposal_id": proposal["proposal_id"],
                "target_role": "engineering",
                "depends_on": [],
            }
        ],
        "alice",
    )
    task_id = result["tasks"][proposal["proposal_id"]]
    assert db.get_task(task_id)["status"] == "AWAITING_APPROVAL"
    assert meetings.approvals.get(task_id)["phase"] == "awaiting_engineering_approval"
    assert (
        db.connection.execute(
            "SELECT COUNT(*) FROM handoffs WHERE task_id=?", (task_id,)
        ).fetchone()[0]
        == 0
    )
    db.close()


@pytest.mark.asyncio
async def test_notes_context_is_scoped_persisted_and_warns_on_failure(tmp_path):
    db, meetings, _ = service(tmp_path, provider=FakeProvider())
    make_meeting(meetings)
    consent(meetings)
    await meetings.transcribe_recording("meeting-1", "meeting.wav", "alice")
    handoff = meetings.request_notes("meeting-1", "alice")
    db.accept_handoff(handoff["handoff_id"], "notes-agent")
    received = {}

    async def context_provider(state, meeting_scope):
        received.update(meeting_scope)
        assert "transcript" not in meeting_scope
        return {
            "verified_facts": [
                {
                    "verification": "verified",
                    "statement": "We decided the API is not ready.",
                    "source_ref": f"task:{handoff['task_id']}:version:7",
                }
            ],
            "untrusted_evidence": [
                {
                    "classification": "untrusted_source_excerpt_not_a_verified_meeting_fact",
                    "content": "Graph mentions an API rollout.",
                    "revision": "rev-123",
                }
            ],
            "warnings": ["Company Brain uses a separately indexed repository."],
        }

    result = await NotesExecutor(
        meetings, agent_id="notes-agent", context_provider=context_provider
    )({"task_id": handoff["task_id"], "handoff_id": handoff["handoff_id"]})
    assert result["status"] == "completed"
    saved = meetings._meeting("meeting-1")["notes"]
    assert saved["context_evidence"]["verified_facts"][0]["source_ref"].startswith("task:")
    assert saved["context_evidence"]["untrusted_evidence"][0]["revision"] == "rev-123"
    assert saved["context_evidence"]["warnings"]
    assert any(row["classification"] == "verified_contradiction" for row in saved["discrepancies"])
    assert received["source"]["room_id"] == "switch-room"

    other_handoff = meetings.request_notes("meeting-1", "alice")
    assert other_handoff == handoff
    db.close()


def test_presence_events_are_authenticated_idempotent_and_record_absence(tmp_path):
    db, meetings, _ = service(tmp_path)
    make_meeting(meetings)
    with pytest.raises(MeetingAuthorizationError):
        meetings.record_presence_event("meeting-1", "bad", "outsider", "join")
    start = datetime.now(UTC)
    joined = meetings.record_presence_event(
        "meeting-1", "evt-1", "bob", "join", occurred_at=start.isoformat()
    )
    left = meetings.record_presence_event(
        "meeting-1",
        "evt-2",
        "bob",
        "leave",
        occurred_at=(start + timedelta(minutes=2)).isoformat(),
    )
    reconnected = meetings.record_presence_event(
        "meeting-1",
        "evt-3",
        "bob",
        "join",
        occurred_at=(start + timedelta(minutes=5)).isoformat(),
    )
    assert joined["event_type"] == "joined"
    assert left["event_type"] == "left"
    assert reconnected["event_type"] == "reconnected"
    assert reconnected["absent_seconds"] == 180
    assert (
        meetings.record_presence_event(
            "meeting-1",
            "evt-3",
            "bob",
            "join",
            occurred_at=(start + timedelta(minutes=5)).isoformat(),
        )["duplicate"]
        is True
    )
    with pytest.raises(ConflictError):
        meetings.record_presence_event(
            "meeting-1",
            "evt-3",
            "bob",
            "leave",
            occurred_at=(start + timedelta(minutes=5)).isoformat(),
        )
    db.close()


@pytest.mark.asyncio
async def test_missing_consent_revocation_partial_failure_and_restart_are_safe(tmp_path):
    db, meetings, _ = service(tmp_path, provider=FakeProvider())
    make_meeting(meetings)
    assert (await meetings.transcribe_recording("meeting-1", "meeting.wav", "alice"))[
        "status"
    ] == "blocked"
    consent(meetings)
    result = await meetings.transcribe_recording("meeting-1", "meeting.wav", "alice")
    assert result["status"] == "completed"
    provider = FakeProvider(failure=True)
    failed_db, failed, _ = service(tmp_path / "failed", provider=provider)
    make_meeting(failed)
    consent(failed)
    pending = await failed.transcribe_recording("meeting-1", "meeting.wav", "alice")
    assert pending["status"] == "pending_execution"
    assert provider.calls == 1
    replay = await failed.transcribe_recording("meeting-1", "meeting.wav", "alice")
    assert replay["status"] == "pending_execution"
    assert provider.calls == 1

    restarted = MeetingService(
        Store(tmp_path / "meetings.sqlite3"),
        FakeProvider(failure=True),
        tmp_path / "recordings",
        pm_id="pm-agent",
        notes_agent_id="notes-agent",
        specialist_targets={"research": "research-agent"},
        human_approvers=["alice"],
    )
    replayed = await restarted.transcribe_recording("meeting-1", "meeting.wav", "alice")
    assert replayed["status"] == "completed"
    assert restarted.provider.calls == 0
    meetings.revoke_consent("meeting-1", "bob", "bob")
    assert (await meetings.transcribe_recording("meeting-1", "meeting.wav", "alice"))[
        "status"
    ] == "blocked"
    assert (
        db.connection.execute(
            "SELECT transcript_json FROM meetings WHERE id='meeting-1'"
        ).fetchone()[0]
        is None
    )
    db.close()
    failed_db.close()
    restarted.store.close()


@pytest.mark.asyncio
async def test_consent_revoke_during_provider_request_discards_transcript(tmp_path):
    entered, release = asyncio.Event(), asyncio.Event()
    db, meetings, _ = service(tmp_path, provider=FakeProvider(wait=(entered, release)))
    make_meeting(meetings)
    consent(meetings)
    pending = asyncio.create_task(
        meetings.transcribe_recording("meeting-1", "meeting.wav", "alice")
    )
    await entered.wait()
    meetings.revoke_consent("meeting-1", "bob", "bob")
    release.set()
    result = await pending
    assert result["status"] == "blocked"
    assert (
        db.connection.execute(
            "SELECT transcript_json FROM meetings WHERE id='meeting-1'"
        ).fetchone()[0]
        is None
    )
    db.close()


def test_notes_discrepancies_only_claim_verified_when_explicit_baseline_matches():
    transcript = {
        "words": [
            utterance("The API is ready.", 0.1, "speaker_0"),
            utterance("The API is not ready.", 3.0, "speaker_1"),
        ]
    }
    provisional = build_meeting_notes("m1", transcript)
    assert all(
        finding["classification"] != "verified_contradiction"
        for finding in provisional["discrepancies"]
    )
    verified = build_meeting_notes(
        "m1",
        transcript,
        verified_facts=[
            {
                "statement": "The API is ready.",
                "source_ref": "decision-1",
                "verification": "verified",
            }
        ],
    )
    assert any(
        finding["classification"] == "verified_contradiction"
        for finding in verified["discrepancies"]
    )
