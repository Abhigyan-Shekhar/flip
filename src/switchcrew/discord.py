"""Trusted identity normalization for Switch's built-in Discord text bridge.

SSE sender IDs are opaque Switch participant IDs. Discord IDs and roles are
NOT present on the documented SSE envelope; operator-verified bindings supply
those facts. Never extract identity or authorization from message bodies.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class DiscordBinding:
    room_id: str
    bridge_id: str
    guild_id: str
    channel_id: str
    participant_id: str
    user_id: str
    roles: tuple[str, ...] = ()


@dataclass(frozen=True)
class DiscordSource:
    guild_id: str
    channel_id: str
    user_id: str
    room_id: str
    participant_id: str
    message_id: str
    thread_id: str | None
    body: str
    roles: tuple[str, ...]


def normalize_discord_event(
    event: dict,
    bindings: list[DiscordBinding],
    *,
    allowed_guilds: set[str],
    allowed_users: set[str],
) -> DiscordSource:
    if event.get("type") != "message":
        raise ValueError("Not a message event")
    payload = event.get("payload")
    if not isinstance(payload, dict):
        raise ValueError("Missing trusted message envelope")
    # Platform messages can speak on behalf of another participant. They are
    # never an authenticated human request for backend purposes.
    if payload.get("sender_kind") == "platform" or payload.get("on_behalf_of"):
        raise ValueError("Platform proxy is not a human identity")
    matches = [
        b
        for b in bindings
        if b.room_id == event.get("room_id")
        and b.bridge_id == event.get("bridge_id")
        and b.participant_id == payload.get("sender")
    ]
    if len(matches) != 1:
        raise ValueError("Missing or ambiguous verified Discord binding")
    binding = matches[0]
    if binding.guild_id not in allowed_guilds or binding.user_id not in allowed_users:
        raise ValueError("Discord source is not allowlisted")
    if not isinstance(payload.get("message_id"), str) or not payload["message_id"]:
        raise ValueError("Stable source message ID required")
    if not isinstance(payload.get("body"), str):
        raise ValueError("Message body required")
    thread = payload.get("thread_id")
    if thread is not None and not isinstance(thread, str):
        raise ValueError("Invalid thread context")
    return DiscordSource(
        binding.guild_id,
        binding.channel_id,
        binding.user_id,
        binding.room_id,
        binding.participant_id,
        payload["message_id"],
        thread,
        payload["body"],
        binding.roles,
    )
