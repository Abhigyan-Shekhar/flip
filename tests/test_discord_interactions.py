import json
from datetime import UTC, datetime

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from switchcrew.discord_interactions import respond_to_interaction, slash_manifest
from switchcrew.settings import Settings
from switchcrew.store import Store


def setup(tmp_path):
    private = Ed25519PrivateKey.generate()
    public = (
        private.public_key()
        .public_bytes(encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw)
        .hex()
    )
    settings = Settings(
        _env_file=None,
        discord_application_public_key=public,
        discord_guild_ids=["guild"],
        discord_user_ids=["alice"],
        discord_bindings=[
            {
                "room_id": "room",
                "bridge_id": "bridge",
                "guild_id": "guild",
                "channel_id": "thread",
                "participant_id": "participant",
                "user_id": "alice",
            }
        ],
    )
    store = Store(tmp_path / "tasks.sqlite3")
    task = store.create_task(
        "Explain the test", {"room_id": "room", "thread_id": "thread"}, ["Evidence"]
    )
    return private, settings, store, task


def signed(private, document, now=None):
    payload = json.dumps(document).encode()
    timestamp = str(int((now or datetime.now(UTC)).timestamp()))
    signature = private.sign(timestamp.encode() + payload).hex()
    return payload, {"x-signature-timestamp": timestamp, "x-signature-ed25519": signature}


def test_signed_slash_command_is_scoped_and_read_only(tmp_path):
    private, settings, store, task = setup(tmp_path)
    request = {
        "type": 2,
        "id": "interaction",
        "guild_id": "guild",
        "channel_id": "thread",
        "member": {"user": {"id": "alice"}},
        "data": {
            "name": "crew",
            "options": [{"name": "status", "options": [{"name": "task_id", "value": task["id"]}]}],
        },
    }
    payload, headers = signed(private, request)
    response = respond_to_interaction(store, settings, payload, headers)
    assert response["type"] == 4
    assert response["data"]["flags"] == 64
    assert "DRAFT" in response["data"]["content"]
    assert len(slash_manifest()[0]["options"]) == 6
    request["member"]["user"]["id"] = "mallory"
    payload, headers = signed(private, request)
    with pytest.raises(PermissionError, match="authorized"):
        respond_to_interaction(store, settings, payload, headers)
    store.close()


def test_discord_signature_and_timestamp_are_verified(tmp_path):
    private, settings, store, _ = setup(tmp_path)
    payload, headers = signed(private, {"type": 1})
    assert respond_to_interaction(store, settings, payload, headers) == {"type": 1}
    with pytest.raises(PermissionError, match="signature"):
        respond_to_interaction(store, settings, payload + b" ", headers)
    headers["x-signature-timestamp"] = "1000000000"
    with pytest.raises(PermissionError, match="Expired"):
        respond_to_interaction(store, settings, payload, headers)
    store.close()


@pytest.mark.parametrize(
    "changes",
    [
        {"member": {"user": ["alice"]}},
        {"member": ["alice"]},
        {"data": ["crew"]},
        {"data": {"name": "crew", "options": 1}},
        {"data": {"name": "crew", "options": {"status": {}}}},
    ],
)
def test_malformed_signed_command_returns_client_error(tmp_path, changes):
    from fastapi.testclient import TestClient
    from pydantic import SecretStr

    from switchcrew.api import create_app

    private, settings, store, _ = setup(tmp_path)
    settings.database_path = tmp_path / "http.sqlite3"
    settings.api_token = SecretStr("test-api-token-" * 3)
    document = {
        "type": 2,
        "guild_id": "guild",
        "channel_id": "thread",
        "member": {"user": {"id": "alice"}},
        "data": {"name": "crew", "options": []},
        **changes,
    }
    payload, headers = signed(private, document)
    with TestClient(create_app(settings)) as client:
        response = client.post("/discord/interactions", content=payload, headers=headers)
        assert response.status_code == 400
    store.close()


def thread_request(task_id):
    return {
        "type": 2,
        "id": "interaction-thread",
        "guild_id": "guild",
        "channel_id": "discord-thread",
        "channel": {"id": "discord-thread", "type": 11, "parent_id": "thread"},
        "member": {"user": {"id": "alice"}},
        "data": {
            "name": "crew",
            "options": [{"name": "status", "options": [{"name": "task_id", "value": task_id}]}],
        },
    }


def configure_thread(settings):
    settings.discord_thread_bindings = [
        {
            "guild_id": "guild",
            "channel_id": "thread",
            "thread_id": "discord-thread",
            "room_id": "room",
            "switch_thread_id": "sw-root",
        }
    ]


def test_real_discord_thread_uses_verified_switch_root(tmp_path):
    private, settings, store, _ = setup(tmp_path)
    configure_thread(settings)
    task = store.create_task(
        "Scoped task", {"room_id": "room", "message_id": "sw-root"}, ["Evidence"]
    )
    payload, headers = signed(private, thread_request(task["id"]))
    response = respond_to_interaction(store, settings, payload, headers)
    assert response["data"]["flags"] == 64
    assert f"Task {task['id']}: DRAFT" in response["data"]["content"]
    assert store.get_task(task["id"])["version"] == task["version"]
    store.close()


@pytest.mark.parametrize("change", ["missing", "duplicate", "parent", "room", "identity"])
def test_thread_mapping_fails_closed(tmp_path, change):
    private, settings, store, task = setup(tmp_path)
    configure_thread(settings)
    request = thread_request(task["id"])
    if change == "missing":
        settings.discord_thread_bindings = []
    elif change == "duplicate":
        settings.discord_thread_bindings *= 2
    elif change == "parent":
        request["channel"]["parent_id"] = "other-parent"
    elif change == "room":
        settings.discord_thread_bindings[0]["room_id"] = "other-room"
    elif change == "identity":
        request["channel"]["id"] = "other-thread"
    payload, headers = signed(private, request)
    with pytest.raises(PermissionError):
        respond_to_interaction(store, settings, payload, headers)
    store.close()


def test_verified_thread_cannot_read_other_thread_task(tmp_path):
    private, settings, store, _ = setup(tmp_path)
    configure_thread(settings)
    task = store.create_task(
        "Private other thread", {"room_id": "room", "thread_id": "sw-other-root"}, ["Evidence"]
    )
    payload, headers = signed(private, thread_request(task["id"]))
    response = respond_to_interaction(store, settings, payload, headers)
    assert "Private other thread" not in response["data"]["content"]
    assert "DRAFT" not in response["data"]["content"]
    store.close()
