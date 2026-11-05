"""Signed Discord inspection and separately authorized, exact-SHA merge controls."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .crew import handle_crew_command
from .discord import DiscordBinding, DiscordSource, DiscordThreadBinding

_COMMANDS = ("status", "team", "why", "dependencies", "trace", "evidence")
_FLIP_COMMANDS = ("status", "logs", "findings", "tasks", "help")
_MERGE_COMMANDS = ("merge-status", "approve-merge", "merge")
_APPROVAL_COMMANDS = (
    "approve-engineering",
    "reject-engineering",
    "approve-release",
    "reject-release",
)
_HEX = re.compile(r"^[0-9a-fA-F]+$")


def slash_manifest() -> list[dict]:
    manifests = [
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

    flip = {
        "name": "flip",
        "description": "Inspect Flip tasks and sandbox receipts",
        "type": 1,
        "options": [
            {
                "name": command,
                "description": f"Inspect {command}",
                "type": 1,
                **(
                    {
                        "options": [
                            {
                                "name": "task_id",
                                "description": "Exact Flip task UUID",
                                "type": 3,
                                "required": True,
                            }
                        ]
                    }
                    if command not in {"tasks", "help"}
                    else {}
                ),
            }
            for command in _FLIP_COMMANDS
        ],
    }
    for name in _APPROVAL_COMMANDS:
        args = [
            {"name": "task_id", "description": "Exact task UUID", "type": 3, "required": True},
            {
                "name": "version",
                "description": "Current task version",
                "type": 4,
                "min_value": 1,
                "required": True,
            },
        ]
        if name.endswith("release"):
            args.append(
                {
                    "name": "revision",
                    "description": "Verified full head SHA",
                    "type": 3,
                    "required": True,
                }
            )
        flip["options"].append(
            {"name": name, "description": "Explicit human " + name, "type": 1, "options": args}
        )
    for name in _MERGE_COMMANDS:
        args = [{"name": "task_id", "description": "Exact task UUID", "type": 3, "required": True}]
        if name != "merge-status":
            args.append(
                {
                    "name": "revision",
                    "description": "Verified full 40-character head SHA",
                    "type": 3,
                    "required": True,
                }
            )
        if name == "approve-merge":
            args.append(
                {
                    "name": "version",
                    "description": "Merge version from merge-status",
                    "type": 4,
                    "min_value": 0,
                    "required": True,
                }
            )
        flip["options"].append(
            {"name": name, "description": f"Guarded {name}", "type": 1, "options": args}
        )
    return [*manifests, flip]


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


def _resolve(settings, payload: bytes, headers, *, now=None):
    if len(payload) > 32768:
        raise ValueError("Discord interaction exceeds byte limit")
    document = _verified(payload, headers, settings.discord_application_public_key, now=now)
    if document.get("type") == 1:
        return document, None, None, {}
    if document.get("type") != 2:
        raise ValueError("Only slash command interactions are supported")
    guild = document.get("guild_id")
    channel = document.get("channel_id")
    member = document.get("member") or {}
    if not isinstance(member, dict) or not isinstance(member.get("user"), dict):
        raise ValueError("Discord member must contain a user object")
    if member["user"].get("bot") or member["user"].get("system"):
        raise PermissionError("Human Discord identity required")
    user = member["user"].get("id")
    if guild not in settings.discord_guild_ids or user not in settings.discord_user_ids:
        raise PermissionError("Discord user/guild not authorized")
    parent_channel = channel
    switch_thread = channel
    mapped_room = None
    channel_info = document.get("channel") or {}
    if not isinstance(channel_info, dict):
        raise ValueError("Discord channel must be an object")
    if channel_info.get("type") in {10, 11, 12}:
        if channel_info.get("id") != channel:
            raise PermissionError("Discord thread identity mismatch")
        parent_channel = channel_info.get("parent_id")
        threads = [
            thread
            for item in settings.discord_thread_bindings
            if (thread := DiscordThreadBinding(**item)).guild_id == guild
            and thread.thread_id == channel
            and thread.channel_id == parent_channel
        ]
        if len(threads) != 1 or not isinstance(threads[0].switch_thread_id, str):
            raise PermissionError("A unique verified Discord thread mapping is required")
        switch_thread = threads[0].switch_thread_id
        mapped_room = threads[0].room_id
        if not switch_thread or not mapped_room:
            raise PermissionError("Discord thread mapping is incomplete")
    matches = [
        b
        for item in settings.discord_bindings
        if (b := DiscordBinding(**item)).guild_id == guild
        and b.channel_id == parent_channel
        and b.user_id == user
        and (mapped_room is None or b.room_id == mapped_room)
    ]
    if len(matches) != 1:
        raise PermissionError("An exact unique verified Discord binding is required")
    data = document.get("data") or {}
    if not isinstance(data, dict):
        raise ValueError("Discord command data must be an object")
    options = data.get("options") or []
    if (
        not isinstance(options, list)
        or data.get("name") not in {"crew", "flip"}
        or len(options) != 1
    ):
        raise ValueError("Unknown slash command")
    selected = options[0]
    args = selected.get("options", []) if isinstance(selected, dict) else []
    commands = (
        _COMMANDS
        if data["name"] == "crew"
        else (*_FLIP_COMMANDS, *_MERGE_COMMANDS, *_APPROVAL_COMMANDS)
    )
    no_task = isinstance(selected, dict) and selected.get("name") in {"tasks", "help"}
    if (
        not isinstance(selected, dict)
        or selected.get("name") not in commands
        or not isinstance(args, list)
    ):
        raise ValueError("Unsupported slash arguments")
    name = selected["name"]
    expected = set() if no_task else {"task_id"}
    if name in {"approve-merge", "merge"}:
        expected.add("revision")
    if name == "approve-merge" or name in _APPROVAL_COMMANDS:
        expected.add("version")
    if name in _APPROVAL_COMMANDS and name.endswith("release"):
        expected.add("revision")
    if any(not isinstance(arg, dict) or not isinstance(arg.get("name"), str) for arg in args):
        raise ValueError("Unsupported slash arguments")
    values = {arg["name"]: arg.get("value") for arg in args}
    if len(values) != len(args) or set(values) != expected:
        raise ValueError("Unsupported slash arguments")
    if not no_task and not isinstance(values["task_id"], str):
        raise ValueError("Task UUID required")
    if "revision" in values and not re.fullmatch(r"[a-f0-9]{40}", str(values["revision"])):
        raise ValueError("An exact full revision is required")
    if "version" in values and (type(values["version"]) is not int or values["version"] < 0):
        raise ValueError("A nonnegative merge version is required")
    binding = matches[0]
    source = DiscordSource(
        guild,
        parent_channel,
        user,
        binding.room_id,
        binding.participant_id,
        document.get("id", ""),
        switch_thread,
        f"/{data['name']} {selected['name']}" + ("" if no_task else f" {values['task_id']}"),
        binding.roles,
    )
    return document, source, name, values


def respond_to_interaction(store, settings, payload: bytes, headers, *, now=None) -> dict:
    document, source, name, values = _resolve(settings, payload, headers, now=now)
    if source is None:
        return {"type": 1}
    if name in _MERGE_COMMANDS:
        raise ValueError("Merge commands require the configured asynchronous release service")
    if name in _APPROVAL_COMMANDS:
        from .approval import ApprovalWorkflow
        from .crew import _task_scope

        task = store.get_task(values["task_id"])
        if not task or not _task_scope(task, source):
            raise PermissionError("Approval task is unavailable in this thread")
        stage = name.split("-", 1)[1]
        approvals = ApprovalWorkflow(store, settings.approval_user_ids)
        data = approvals.get(task["id"])
        if stage == "release" and (not data or data.get("revision") != values["revision"]):
            raise PermissionError("Release approval requires the exact verified revision")
        approvals.decide(
            task["id"], stage, name.startswith("approve-"), source.user_id, values["version"]
        )
        return {
            "type": 4,
            "data": {"content": "Explicit human " + stage + " decision recorded.", "flags": 64},
        }
    response = handle_crew_command(store, source, can_cancel=False)
    message = (response or {}).get("text") or "Task is unavailable in this thread."
    return {"type": 4, "data": {"content": message[:1900], "flags": 64}}


async def _merge_result(service, source, name, values):
    if name == "merge-status":
        result = await service.inspect(values["task_id"])
        return (
            f"PR #{result['pr_number']}; revision {result['revision']}\n"
            f"Merge phase: {result['merge']['phase']}; version {result['merge']['version']}\n"
            "Approve using /flip approve-merge with this task, revision and merge version. "
            "Then explicitly execute /flip merge with the same task and revision."
        )
    if name == "approve-merge":
        result = await service.approve(
            values["task_id"], source.user_id, values["version"], values["revision"]
        )
        return (
            f"Human merge approval recorded; phase {result['phase']}; version {result['version']}."
        )
    result = await service.execute(values["task_id"], source.user_id, values["revision"])
    return (
        f"Merge status: {result['status']}; revision {result['revision']}\n"
        f"Merge commit: {result.get('merge_commit_sha', 'pending reconciliation')}"
    )


async def _finish_merge(service, source, name, values, application_id, token):
    import httpx

    from .store import ConflictError

    try:
        content = await _merge_result(service, source, name, values)
    except (PermissionError, ValueError, ConflictError):
        content = (
            "Merge action refused: current authorization, revision or verification "
            "gates did not pass."
        )
    except Exception:
        content = (
            "Merge outcome is unavailable. Inspect merge status before retrying; "
            "no automatic retry was made."
        )
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.patch(
                f"https://discord.com/api/v10/webhooks/{application_id}/{token}/messages/@original",
                json={"content": content[:1900], "allowed_mentions": {"parse": []}},
            )
    except httpx.HTTPError:
        # Interaction credentials are transient and must not enter exception logs.
        pass


async def respond_to_interaction_async(
    store, settings, payload, headers, *, merge_service=None, background_tasks=None, now=None
):
    from .crew import _task_scope

    document, source, name, values = _resolve(settings, payload, headers, now=now)
    if source is None or name not in _MERGE_COMMANDS:
        return respond_to_interaction(store, settings, payload, headers, now=now)
    if merge_service is None:
        raise PermissionError("Guarded merge is not configured")
    task = store.get_task(values["task_id"])
    if not task or not _task_scope(task, source):
        raise PermissionError("Merge task is unavailable in this thread")
    if name != "merge-status" and source.user_id not in settings.approval_user_ids:
        raise PermissionError("An explicitly configured Discord human approver is required")
    if background_tasks is None:
        raise ValueError("Deferred Discord acknowledgement is required for merge commands")
    application_id, token = document.get("application_id"), document.get("token")
    if not isinstance(application_id, str) or not re.fullmatch(r"[0-9]{1,30}", application_id):
        raise ValueError("Discord application identity required")
    if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,500}", token):
        raise ValueError("Discord interaction callback required")
    background_tasks.add_task(
        _finish_merge, merge_service, source, name, values, application_id, token
    )
    return {"type": 5, "data": {"flags": 64}}
