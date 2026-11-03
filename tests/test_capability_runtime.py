import json

import pytest

from switchcrew import runtime
from switchcrew.store import Store


def registry(path, role):
    data = {
        "role": role,
        "agent_id": role,
        "participant_id": "participant-" + role,
        "name": role,
        "api_key": "private",
        "room_id": "room",
        "allowed_senders": {"participant-pm": "pm"},
        "target_names": {"pm": "pm"},
    }
    path.write_text(json.dumps(data))
    path.chmod(0o600)
    return data


@pytest.mark.asyncio
async def test_capability_runtime_refuses_public_registry_and_shared_voice_ledger(tmp_path):
    credential = tmp_path / "registry.json"
    registry(credential, "voice")
    config = tmp_path / "voice.json"
    database = tmp_path / "application.db"
    config.write_text(json.dumps({"role": "voice", "ledger": str(database)}))
    config.chmod(0o600)
    credential.chmod(0o644)
    with pytest.raises(ValueError, match="registry must be private"):
        await runtime._main("voice", credential, database, None, config)
    credential.chmod(0o600)
    with pytest.raises(ValueError, match="separate files"):
        await runtime._main("voice", credential, database, None, config)
    assert not database.exists()


@pytest.mark.asyncio
async def test_actual_runtime_cli_wiring_executes_documentation_capability(tmp_path, monkeypatch):
    registry_file = tmp_path / "registry.json"
    registry(registry_file, "documentation")
    source = tmp_path / "sources"
    source.mkdir()
    (source / "guide.md").write_text("# Handoffs\nDurable handoffs survive worker restarts.\n")
    config = tmp_path / "documentation.json"
    config.write_text(
        json.dumps(
            {
                "role": "documentation",
                "capability": {"root": str(source), "sources": ["guide.md"]},
                "memory": {"checkpoint_path": str(tmp_path / "checkpoints.db")},
            }
        )
    )
    config.chmod(0o600)
    database = tmp_path / "state.db"
    store = Store(database)
    task = store.create_task("Document handoffs", {}, ["Describe durable handoffs"])
    task = store.transition_task(task["id"], "PLANNED", 0)
    task = store.transition_task(task["id"], "IN_PROGRESS", task["version"])
    h = store.create_handoff(
        task["id"],
        "pm",
        "documentation",
        task["objective"],
        task["acceptance_criteria"],
        task["version"],
    )
    store.close()

    class Switch:
        connected = True
        cursor = None
        closed = False

        def __init__(self, *args, **kwargs):
            self.agent_id = "documentation"

        async def connect_to_room(self, room):
            assert room == "room"

        async def send_targeted_message(self, *args, **kwargs):
            return {"target_statuses": {"pm": "live"}, "event_id": "delivered"}

        async def events(self):
            yield {"type": "connection_state"}
            yield {
                "type": "message",
                "room_id": "room",
                "payload": {
                    "sender": "participant-pm",
                    "message_id": "assignment",
                    "body": json.dumps(
                        {"type": "handoff", "task_id": task["id"], "handoff_id": h["id"]}
                    ),
                },
            }

        async def close(self):
            self.closed = True

    monkeypatch.setattr(runtime, "SwitchClient", Switch)
    await runtime._main("documentation", registry_file, database, None, config)
    reopened = Store(database)
    try:
        result = reopened.get_handoff(h["id"])["result"]
        assert result["status"] == "completed"
        assert result["action_performed"] is False
        assert result["sources"][0]["path"] == "guide.md"
        assert "Durable handoffs" in result["artifact"]["content"]
    finally:
        reopened.close()
