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
