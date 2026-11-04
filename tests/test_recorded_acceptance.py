import hashlib
import wave
from datetime import UTC, datetime, timedelta

import pytest

from switchcrew.meetings import MeetingService
from switchcrew.recorded_acceptance import inspect_recorded_meeting
from switchcrew.store import Store


class ControlledTranscription:
    async def transcribe_meeting(self, audio):
        assert audio
        return {
            "text": "We decided to ship. I will write notes by Friday.",
            "words": [
                {
                    "text": "We decided to ship.",
                    "start": 0.1,
                    "end": 1.1,
                    "speaker_id": "speaker_0",
                },
                {
                    "text": "I will write notes by Friday.",
                    "start": 2,
                    "end": 3,
                    "speaker_id": "speaker_1",
                },
            ],
        }

    def spec(self):
        return {"provider": "controlled-test"}


@pytest.mark.asyncio
async def test_recorded_acceptance_never_claims_switch_handoff_without_it(tmp_path):
    recordings = tmp_path / "recordings"
    recordings.mkdir()
    with wave.open(str(recordings / "consented.wav"), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b"\0\0" * 16000)
    path = tmp_path / "meetings.sqlite3"
    store = Store(path)
    meetings = MeetingService(
        store,
        ControlledTranscription(),
        recordings,
        pm_id="pm",
        notes_agent_id="notes",
        specialist_targets={},
    )
    meetings.create_meeting("m-1", "discord-channel", "discord-thread", "alice", ["alice", "bob"])
    before = inspect_recorded_meeting(path, "m-1", recordings_root=recordings)
    assert not before["checks"]["all_participants_consented"]
    assert before["status"] == "incomplete"
    for actor in ("alice", "bob"):
        meetings.grant_consent(
            "m-1",
            actor,
            actor,
            evidence_id=f"consent-{actor}",
            expires_at=(datetime.now(UTC) + timedelta(hours=1)).isoformat(),
            disclosure_acknowledged=True,
        )
    output = await meetings.transcribe_recording("m-1", "consented.wav", "alice")
    assert output["status"] == "completed"
    meetings.prepare_notes("m-1", "notes")
    report = inspect_recorded_meeting(path, "m-1", recordings_root=recordings)
    assert report["checks"]["all_participants_consented"]
    assert report["checks"]["recorded_source_pinned"]
    assert report["checks"]["transcript_digest_valid"]
    assert report["checks"]["notes_digest_valid"]
    assert not report["checks"]["registered_voice_handoff"]
    assert report["status"] == "incomplete"
    assert report["live_discord_capture"] == "not_verified"
    meetings.revoke_consent("m-1", "bob", "bob")
    revoked = inspect_recorded_meeting(path, "m-1", recordings_root=recordings)
    assert not revoked["checks"]["all_participants_consented"]
    assert not revoked["checks"]["meeting_not_revoked"]
    assert revoked["status"] == "incomplete"
    store.close()


@pytest.mark.asyncio
async def test_recorded_acceptance_requires_completed_switch_results_and_intact_audio(tmp_path):
    recordings = tmp_path / "recordings"
    recordings.mkdir()
    recording = recordings / "consented.wav"
    with wave.open(str(recording), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b"\0\0" * 16000)
    audio_sha = hashlib.sha256(recording.read_bytes()).hexdigest()
    path = tmp_path / "meetings.sqlite3"
    store = Store(path)
    meetings = MeetingService(
        store,
        ControlledTranscription(),
        recordings,
        pm_id="pm",
        notes_agent_id="notes",
        specialist_targets={},
        recording_manifest={
            "consented.wav": {
                "meeting_id": "m-2",
                "owner_id": "alice",
                "participants": ["alice", "bob"],
                "sha256": audio_sha,
            }
        },
        require_recording_authorization=True,
    )
    meetings.create_meeting(
        "m-2",
        "discord-channel",
        "discord-thread",
        "alice",
        ["alice", "bob"],
        source={"room_id": "switch-room"},
    )
    for actor in ("alice", "bob"):
        meetings.grant_consent(
            "m-2",
            actor,
            actor,
            evidence_id=f"consent-{actor}",
            expires_at=(datetime.now(UTC) + timedelta(hours=1)).isoformat(),
            disclosure_acknowledged=True,
        )
    job = meetings.request_transcription("m-2", "consented.wav", "alice", voice_agent_id="voice")
    store.accept_handoff(job["handoff_id"], "voice")
    voice_result = await meetings.transcribe_for_handoff(
        {"task_id": job["task_id"], "handoff_id": job["handoff_id"]}, "voice"
    )
    assert voice_result["status"] == "completed"
    pending_voice = inspect_recorded_meeting(path, "m-2", recordings_root=recordings)
    assert not pending_voice["checks"]["registered_voice_handoff"]
    store.complete_handoff(job["handoff_id"], "voice", voice_result)

    notes_job = meetings.request_notes("m-2", "alice")
    meetings.prepare_notes("m-2", "alice")  # Bypass the Notes agent deliberately.
    pending_notes = inspect_recorded_meeting(path, "m-2", recordings_root=recordings)
    assert pending_notes["checks"]["registered_voice_handoff"]
    assert pending_notes["checks"]["recording_file_digest_valid"]
    assert not pending_notes["checks"]["notes_assignment_registered"]
    assert pending_notes["status"] == "incomplete"

    store.accept_handoff(notes_job["handoff_id"], "notes")
    notes_result = meetings.notes_for_handoff(
        {"task_id": notes_job["task_id"], "handoff_id": notes_job["handoff_id"]},
        "notes",
    )
    store.complete_handoff(notes_job["handoff_id"], "notes", notes_result)
    complete = inspect_recorded_meeting(path, "m-2", recordings_root=recordings)
    assert complete["status"] == "recorded_artifacts_valid"
    assert all(complete["checks"].values())

    recording.write_bytes(b"changed recording")
    tampered = inspect_recorded_meeting(path, "m-2", recordings_root=recordings)
    assert not tampered["checks"]["recording_file_digest_valid"]
    assert tampered["status"] == "incomplete"
    store.close()
