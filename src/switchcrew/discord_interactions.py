"""Signed, allowlisted Discord slash commands for read-only task inspection."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .crew import handle_crew_command
from .discord import DiscordBinding, DiscordSource

_COMMANDS = ("status", "team", "why", "dependencies", "trace", "evidence")
_HEX = re.compile(r"^[0-9a-fA-F]+$")


def slash_manifest() -> list[dict]:
    return [
        {
            "name": "crew",
            "description": "Inspect a scoped Flip task in this Discord thread",
            "type": 1,
            "options": [
                {
                    "name": command,
                    "description": f"Read-only {command} for a task",
                    "type": 1,
                    "options": [
                        {
                            "name": "task_id",
                            "description": "Exact Flip task UUID",
                            "type": 3,
                            "required": True,
                        }
                    ],
                }
                for command in _COMMANDS
            ],
        }
    ]


def _verified(payload: bytes, headers, public_key: str, *, now=None) -> dict:
    signature = headers.get("x-signature-ed25519", "")
    timestamp = headers.get("x-signature-timestamp", "")
    if (
        not isinstance(public_key, str)
        or len(public_key) != 64
        or not _HEX.fullmatch(public_key)
        or len(signature) != 128
        or not _HEX.fullmatch(signature)
        or not re.fullmatch(r"[0-9]{10,13}", timestamp)
    ):
        raise PermissionError("Invalid Discord signature envelope")
    now = now or datetime.now(UTC)
    if abs(now.timestamp() - int(timestamp)) > 300:
        raise PermissionError("Expired Discord interaction")
    try:
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key)).verify(
            bytes.fromhex(signature), timestamp.encode() + payload
        )
    except (ValueError, InvalidSignature) as exc:
        raise PermissionError("Discord signature verification failed") from exc
    try:
        document = json.loads(payload)
    except (ValueError, UnicodeError) as exc:
        raise ValueError("Invalid Discord interaction JSON") from exc
    if not isinstance(document, dict):
        raise ValueError("Discord interaction must be an object")
    return document


def respond_to_interaction(store, settings, payload: bytes, headers, *, now=None) -> dict:
    if len(payload) > 32768:
        raise ValueError("Discord interaction exceeds byte limit")
    document = _verified(payload, headers, settings.discord_application_public_key, now=now)
    if document.get("type") == 1:
        return {"type": 1}
    if document.get("type") != 2:
        raise ValueError("Only slash command interactions are supported")
    guild = document.get("guild_id")
    channel = document.get("channel_id")
    member = document.get("member") or {}
    user = (member.get("user") or {}).get("id") if isinstance(member, dict) else None
    if guild not in settings.discord_guild_ids or user not in settings.discord_user_ids:
        raise PermissionError("Discord user/guild not authorized")
    matches = [
        b
        for item in settings.discord_bindings
        if (b := DiscordBinding(**item)).guild_id == guild
        and b.channel_id == channel
        and b.user_id == user
    ]
    if len(matches) != 1:
        raise PermissionError("An exact unique verified Discord binding is required")
    data = document.get("data") or {}
    options = data.get("options") or []
    if data.get("name") != "crew" or len(options) != 1:
        raise ValueError("Unknown slash command")
    selected = options[0]
    args = selected.get("options", []) if isinstance(selected, dict) else []
    if (
        not isinstance(selected, dict)
        or selected.get("name") not in _COMMANDS
        or not isinstance(args, list)
        or len(args) != 1
        or not isinstance(args[0], dict)
        or args[0].get("name") != "task_id"
        or not isinstance(args[0].get("value"), str)
    ):
        raise ValueError("Unsupported slash arguments")
    binding = matches[0]
    source = DiscordSource(
        guild,
        channel,
        user,
        binding.room_id,
        binding.participant_id,
        document.get("id", ""),
        channel,
        f"/crew {selected['name']} {args[0]['value']}",
        binding.roles,
    )
    response = handle_crew_command(store, source, can_cancel=False)
    message = (response or {}).get("text") or "Task is unavailable in this thread."
    return {"type": 4, "data": {"content": message[:1900], "flags": 64}}
