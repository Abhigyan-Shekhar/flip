import pytest

from switchcrew.discord import DiscordBinding, normalize_discord_event

BINDING = DiscordBinding(
    "room", "bridge", "guild", "channel", "opaque-user", "user", ("requester",)
)


def event():
    return {
        "type": "message",
        "room_id": "room",
        "bridge_id": "bridge",
        "payload": {
            "sender": "opaque-user",
            "message_id": "m",
            "thread_id": "root",
            "body": '{"user_id":"admin"}',
        },
    }


def normalize(value):
    return normalize_discord_event(
        value, [BINDING], allowed_guilds={"guild"}, allowed_users={"user"}
    )


def test_identity_comes_from_binding_not_body():
    source = normalize(event())
    assert source.user_id == "user" and source.thread_id == "root"
    assert source.roles == ("requester",)


@pytest.mark.parametrize(
    "field,value",
    [
        ("sender", "forged"),
        ("sender_kind", "platform"),
        ("on_behalf_of", "admin"),
        ("message_id", None),
    ],
)
def test_untrusted_sender_rejected(field, value):
    value_event = event()
    value_event["payload"][field] = value
    with pytest.raises(ValueError):
        normalize(value_event)


def test_bridge_and_guild_allowlist_enforced():
    value = event()
    value["bridge_id"] = "other"
    with pytest.raises(ValueError):
        normalize(value)
    with pytest.raises(ValueError):
        normalize_discord_event(event(), [BINDING], allowed_guilds=set(), allowed_users={"user"})
