import asyncio
import json
import wave
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from switchcrew.api import create_app
from switchcrew.graphs import build_agent_graph
from switchcrew.organization_runtime import configured_executor
from switchcrew.runtime import AgentConnection, AgentRuntime
from switchcrew.settings import Settings
from switchcrew.wire import read_message

ROOM = "switch-room"
PROJECT = "github.com/acme/service"
API_TOKEN = "x" * 40


def create_wav(path):
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b"\0\0" * 16000)


def word(text, start, speaker):
    return {"text": text, "start": start, "end": start + 1.0, "speaker_id": speaker}


class FakeProvider:
    def __init__(self):
        self.calls = 0

    async def transcribe_meeting(self, audio):
        self.calls += 1
        assert audio
        return {
            "text": "I will update API by Friday. We decided the API is ready.",
            "words": [
                word("I will update API by Friday.", 0.1, "speaker_0"),
                word("We decided the API is ready.", 3.0, "speaker_1"),
            ],
        }

    def spec(self):
        return {"provider": "controlled-test-provider"}


class SwitchMesh:
    def __init__(self):
        self.runtimes = {}
        self.pending = []
        self.sequence = 0

    def client(self, agent_id, participant_id, targets):
        return MeshClient(self, agent_id, participant_id, targets)

    async def drain(self, max_events=20):
        delivered = []
        while self.pending:
            if len(delivered) >= max_events:
                raise AssertionError("Switch handoff pipeline did not quiesce")
            recipient, event = self.pending.pop(0)
            runtime = self.runtimes[recipient]
            accepted = await runtime.receive(event)
            message = read_message(event["payload"]["body"], runtime.store)
            receipt = runtime.store.get_handoff(message["handoff_id"])
            assert accepted, {
                "recipient": recipient,
                "message": message,
                "handoff": receipt,
                "allowed_senders": runtime.connection.allowed_senders,
            }
            await runtime.process_pending()
            await runtime.flush_outbox()
            delivered.append((recipient, event["payload"]["body"]))
        return delivered


class MeshClient:
    connected = True
    cursor = None

    def __init__(self, mesh, agent_id, participant_id, targets):
        self.mesh, self.agent_id = mesh, agent_id
        self.participant_id, self.targets = participant_id, targets
        self.acks = []

    async def connect_to_room(self, room):
        assert room == ROOM

    async def acknowledge(self, sequence):
        self.acks.append(sequence)
        self.cursor = sequence

    async def events(self):
        if False:
            yield None

    async def send_targeted_message(self, body, target_names, thread_id):
        assert len(target_names) == 1
        target_name = target_names[0]
        recipient = self.targets[target_name]
        self.mesh.sequence += 1
        self.mesh.pending.append(
            (
                recipient,
                {
                    "type": "message",
                    "room_id": ROOM,
                    "sequence": self.mesh.sequence,
                    "payload": {
                        "sender": self.participant_id,
                        "message_id": f"transport-{self.mesh.sequence}",
                        "body": body,
                        "thread_id": thread_id,
                    },
                },
            )
        )
        return {"target_statuses": {target_name: "live"}, "event_id": str(self.mesh.sequence)}


def setup_pipeline(tmp_path):
    recordings = tmp_path / "recordings"
    recordings.mkdir()
    create_wav(recordings / "meeting.wav")
    create_wav(recordings / "meeting-no-notes.wav")
    config_path = tmp_path / "organization.json"
    config_path.write_text(
        json.dumps(
            {
                "meetings": {
                    "input_root": str(recordings),
                    "notes_agent_id": "notes-agent",
                    "voice_agent_id": "voice-agent",
                    "provider": {"api_key_env": "TEST_MEETING_API_KEY"},
                    "origins": {
                        "meeting-switch-e2e": {
                            "room_id": ROOM,
                            "channel_id": "discord-channel",
                            "thread_id": "discord-thread",
                            "message_id": "meeting-switch-e2e-message",
                            "guild_id": "guild-1",
                            "owner_id": "alice",
                        },
                        "meeting-no-notes-runtime": {
                            "room_id": ROOM,
                            "channel_id": "discord-channel",
                            "thread_id": "discord-thread",
                            "message_id": "meeting-no-notes-runtime-message",
                            "guild_id": "guild-1",
                            "owner_id": "alice",
                        },
                    },
                    "recording_manifest": {
                        "meeting.wav": {
                            "meeting_id": "meeting-switch-e2e",
                            "owner_id": "alice",
                            "participants": ["alice", "bob"],
                        },
                        "meeting-no-notes.wav": {
                            "meeting_id": "meeting-no-notes-runtime",
                            "owner_id": "alice",
                            "participants": ["alice"],
                        },
                    },
                }
            }
        )
    )
    config_path.chmod(0o600)
    settings = Settings(
        _env_file=None,
        database_path=tmp_path / "workflow.sqlite3",
        api_token=API_TOKEN,
        human_credentials={"alice": "a" * 40, "bob": "b" * 40},
        approval_credentials={"meeting-approver": "m" * 40},
        organization_config=config_path,
        switch_room_id=ROOM,
        memory_project=PROJECT,
        pm_agent_id="pm-agent",
        research_agent_id="research-agent",
        specialist_targets={
            "notes": {"agent_id": "notes-agent"},
            "research": {"agent_id": "research-agent"},
        },
    )
    return create_app(settings), settings


def headers(token):
    return {"Authorization": f"Bearer {token}"}


async def run_notes_switch_handoff(store, settings):
    notes_connection = AgentConnection(
        "notes",
        "notes-agent",
        "notes-participant",
        "Notes",
        "notes-secret",
        ROOM,
        {"pm-participant": "pm-agent"},
        {"pm-agent": "PM"},
    )
    pm_connection = AgentConnection(
        "pm",
        "pm-agent",
        "pm-participant",
        "PM",
        "pm-secret",
        ROOM,
        {"notes-participant": "notes-agent"},
        {"notes-agent": "Notes"},
    )
    mesh = SwitchMesh()
    notes_switch = mesh.client("notes-agent", "notes-participant", {"PM": "pm-agent"})
    pm_switch = mesh.client("pm-agent", "pm-participant", {"Notes": "notes-agent"})
    notes_executor = configured_executor(
        "notes",
        {"mode": "meeting_notes"},
        store,
        notes_connection,
        settings,
    )
    notes_graph = build_agent_graph("notes", executor=notes_executor)
    pm_graph = build_agent_graph("pm")
    notes_runtime = AgentRuntime(notes_connection, store, notes_switch, notes_graph)
    pm_runtime = AgentRuntime(pm_connection, store, pm_switch, pm_graph)
    mesh.runtimes.update({"notes-agent": notes_runtime, "pm-agent": pm_runtime})

    await pm_runtime.flush_outbox()
    delivered = await mesh.drain()
    return delivered


async def run_voice_switch_handoff(store, settings):
    voice_connection = AgentConnection(
        "voice",
        "voice-agent",
        "voice-participant",
        "Voice",
        "voice-secret",
        ROOM,
        {"pm-participant": "pm-agent"},
        {"pm-agent": "PM"},
    )
    pm_connection = AgentConnection(
        "pm",
        "pm-agent",
        "pm-participant",
        "PM",
        "pm-secret",
        ROOM,
        {"voice-participant": "voice-agent"},
        {"voice-agent": "Voice"},
    )
    mesh = SwitchMesh()
    voice_switch = mesh.client("voice-agent", "voice-participant", {"PM": "pm-agent"})
    pm_switch = mesh.client("pm-agent", "pm-participant", {"Voice": "voice-agent"})
    voice_executor = configured_executor(
        "voice",
        {"mode": "meeting_transcription"},
        store,
        voice_connection,
        settings,
    )
    voice_graph = build_agent_graph("voice", executor=voice_executor)
    pm_graph = build_agent_graph("pm")
    voice_runtime = AgentRuntime(voice_connection, store, voice_switch, voice_graph)
    pm_runtime = AgentRuntime(pm_connection, store, pm_switch, pm_graph)
    mesh.runtimes.update({"voice-agent": voice_runtime, "pm-agent": pm_runtime})

    await pm_runtime.flush_outbox()
    delivered = await mesh.drain()
    return delivered


def test_notes_are_persisted_only_after_voice_and_notes_switch_handoffs_and_human_confirmation(
    tmp_path, monkeypatch
):
    app, settings = setup_pipeline(tmp_path)
    provider = FakeProvider()
    monkeypatch.setenv("TEST_MEETING_API_KEY", "controlled-provider-key")
    monkeypatch.setattr("switchcrew.voice.ElevenLabsVoice", lambda *_args, **_kwargs: provider)
    with TestClient(app) as client:
        organizer = headers("a" * 40)
        privileged = headers("m" * 40)
        created = client.post(
            "/meetings",
            headers=organizer,
            json={
                "meeting_id": "meeting-switch-e2e",
                "channel_id": "discord-channel",
                "thread_id": "discord-thread",
                "participants": ["alice", "bob"],
                "source": {"room_id": ROOM},
            },
        )
        assert created.status_code == 200, created.text
        for actor, token in (("alice", "a" * 40), ("bob", "b" * 40)):
            consent = client.post(
                "/meetings/meeting-switch-e2e/consent",
                headers=headers(token),
                json={
                    "evidence_id": f"consent-{actor}",
                    "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
                    "disclosure_acknowledged": True,
                },
            )
            assert consent.status_code == 200, consent.text
        transcript = client.post(
            "/meetings/meeting-switch-e2e/recording",
            headers=organizer,
            json={"filename": "meeting.wav"},
        )
        assert transcript.status_code == 200, transcript.text
        assert transcript.json()["status"] == "queued"
        assert provider.calls == 0
        voice_handoff = app.state.store.get_handoff(transcript.json()["handoff_id"])
        assert voice_handoff["status"] == "pending"
        voice_deliveries = asyncio.run(run_voice_switch_handoff(app.state.store, settings))
        assert len(voice_deliveries) == 3
        voice_handoff = app.state.store.get_handoff(transcript.json()["handoff_id"])
        assert voice_handoff["status"] == "completed"
        assert voice_handoff["result"]["status"] == "completed"
        assert "transcript" not in voice_handoff["result"]
        assert provider.calls == 1

        notes_requested = client.post("/meetings/meeting-switch-e2e/notes", headers=organizer)
        assert notes_requested.status_code == 200, notes_requested.text
        handoff = notes_requested.json()
        before_switch = client.get("/meetings/meeting-switch-e2e/notes", headers=organizer)
        assert before_switch.status_code == 409

        store = app.state.store
        notes_handoff = store.get_handoff(handoff["handoff_id"])
        assert notes_handoff["status"] == "pending"
        delivered = asyncio.run(run_notes_switch_handoff(store, settings))
        assert len(delivered) == 3
        notes_handoff = store.get_handoff(handoff["handoff_id"])
        assert notes_handoff["status"] == "completed"
        assert notes_handoff["result"]["status"] == "completed"
        assert notes_handoff["result"]["meeting_id"] == "meeting-switch-e2e"

        read = client.get("/meetings/meeting-switch-e2e/notes", headers=organizer)
        assert read.status_code == 200, read.text
        notes = read.json()
        assert notes["minutes"]["decisions"]
        assert notes["action_items"][0]["evidence_refs"]
        assert "segments" not in notes

        proposal = notes["action_items"][0]
        action = client.post(
            "/meetings/meeting-switch-e2e/confirm",
            headers=organizer,
            json={"notes_handoff_id": handoff["handoff_id"], "actions": [{}]},
        )
        assert action.status_code == 403

        confirmed = client.post(
            "/meetings/meeting-switch-e2e/confirm",
            headers=privileged,
            json={
                "notes_handoff_id": handoff["handoff_id"],
                "actions": [
                    {
                        "proposal_id": proposal["proposal_id"],
                        "title": "Update API behavior",
                        "acceptance_criteria": ["Verify the API behavior with evidence"],
                        "target_role": "research",
                        "depends_on": [],
                    }
                ],
            },
        )
        assert confirmed.status_code == 200, confirmed.text
        task_id = next(iter(confirmed.json()["tasks"].values()))
        action_handoffs = store.connection.execute(
            "SELECT recipient,question FROM handoffs WHERE task_id=?", (task_id,)
        ).fetchone()
        assert action_handoffs["recipient"] == "research-agent"
        assert "seg-00001" in action_handoffs["question"]
        research_mesh = SwitchMesh()
        pm_connection = AgentConnection(
            "pm",
            "pm-agent",
            "pm-participant",
            "PM",
            "pm-secret",
            ROOM,
            {"research-participant": "research-agent"},
            {"research-agent": "Research"},
        )
        pm_runtime = AgentRuntime(
            pm_connection,
            store,
            research_mesh.client("pm-agent", "pm-participant", {"Research": "research-agent"}),
            build_agent_graph("pm"),
        )
        asyncio.run(pm_runtime.flush_outbox())
        assert len(research_mesh.pending) == 1
        recipient, research_event = research_mesh.pending[0]
        assert recipient == "research-agent"
        switch_handoff = read_message(research_event["payload"]["body"], store)
        assert switch_handoff["type"] == "handoff"
        assert switch_handoff["task_id"] == task_id


def test_missing_notes_runtime_blocks_instead_of_forging_switch_result(tmp_path, monkeypatch):
    app, settings = setup_pipeline(tmp_path)
    provider = FakeProvider()
    monkeypatch.setenv("TEST_MEETING_API_KEY", "controlled-provider-key")
    monkeypatch.setattr("switchcrew.voice.ElevenLabsVoice", lambda *_args, **_kwargs: provider)
    with TestClient(app) as client:
        organizer = headers("a" * 40)
        created = client.post(
            "/meetings",
            headers=organizer,
            json={
                "meeting_id": "meeting-no-notes-runtime",
                "channel_id": "discord-channel",
                "thread_id": "discord-thread",
                "participants": ["alice"],
                "source": {"room_id": ROOM},
            },
        )
        assert created.status_code == 200
        consent = client.post(
            "/meetings/meeting-no-notes-runtime/consent",
            headers=organizer,
            json={
                "evidence_id": "alice-consent",
                "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
                "disclosure_acknowledged": True,
            },
        )
        assert consent.status_code == 200
        transcription = client.post(
            "/meetings/meeting-no-notes-runtime/recording",
            headers=organizer,
            json={"filename": "meeting-no-notes.wav"},
        )
        assert transcription.status_code == 200
        assert transcription.json()["status"] == "queued"
        asyncio.run(run_voice_switch_handoff(app.state.store, settings))
        handoff = client.post("/meetings/meeting-no-notes-runtime/notes", headers=organizer)
        assert handoff.status_code == 200
        notes = client.get("/meetings/meeting-no-notes-runtime/notes", headers=organizer)
        assert notes.status_code == 409
        assert (
            client.post("/meetings/meeting-no-notes-runtime/revoke", headers=organizer).status_code
            == 200
        )
        assert (
            client.get("/meetings/meeting-no-notes-runtime/notes", headers=organizer).status_code
            == 403
        )
        assert (
            client.post(
                "/meetings/meeting-no-notes-runtime/confirm",
                headers=headers("m" * 40),
                json={"notes_handoff_id": handoff.json()["handoff_id"], "actions": [{}]},
            ).status_code
            == 403
        )
