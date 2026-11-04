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
    before = inspect_recorded_meeting(path, "m-1")
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
    report = inspect_recorded_meeting(path, "m-1")
    assert report["checks"]["all_participants_consented"]
    assert report["checks"]["recorded_source_pinned"]
    assert report["checks"]["transcript_digest_valid"]
    assert report["checks"]["notes_digest_valid"]
    assert not report["checks"]["registered_voice_handoff"]
    assert report["status"] == "incomplete"
    assert report["live_discord_capture"] == "not_verified"
    meetings.revoke_consent("m-1", "bob", "bob")
    revoked = inspect_recorded_meeting(path, "m-1")
    assert not revoked["checks"]["all_participants_consented"]
    assert not revoked["checks"]["meeting_not_revoked"]
    assert revoked["status"] == "incomplete"
    store.close()
