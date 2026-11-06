"""Signed Discord inspection and separately authorized, exact-SHA merge controls."""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import replace
from datetime import UTC, datetime

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .crew import handle_crew_command
from .discord import DiscordBinding, DiscordSource, DiscordThreadBinding
from .harnesses import HarnessError
from .site_builder import build_site
from .switch import SwitchClient, SwitchProtocolError
from .vercel import PreviewError, deploy_html, deploy_preview

_COMMANDS = ("status", "team", "why", "dependencies", "trace", "evidence")
_FLIP_COMMANDS = ("status", "logs", "findings", "tasks", "help")
_MERGE_COMMANDS = ("ready-for-review", "merge-status", "approve-merge", "merge")
_APPROVAL_COMMANDS = (
    "approve-engineering",
    "reject-engineering",
    "approve-release",
    "reject-release",
)
_HEX = re.compile(r"^[0-9a-fA-F]+$")
_CONNECTIONS = "agent-connections"
_CONNECTION_AGENTS = ("pm", "research")
_LAUNCH_SITE = "launch-site"
_SITE_INPUTS = {"title": 80, "description": 320}
_BUILD_SITE = "build-site"
_BUILD_INPUTS = {"prompt": 1500, "revision": 30}


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
    connections = {
        "name": _CONNECTIONS,
        "description": "Privately view a Switch agent's MCP service grants",
        "type": 1,
        "options": [
            {
                "name": "agent",
                "description": "SwitchCrew agent",
                "type": 3,
                "required": True,
                "choices": [
                    {"name": role.upper() if role == "pm" else role.title(), "value": role}
                    for role in _CONNECTION_AGENTS
                ],
            }
        ],
    }
    launch = {
        "name": _LAUNCH_SITE,
        "description": "Build and publish a one-page Vercel preview",
        "type": 1,
        "options": [
            {
                "name": name,
                "description": f"{name.title()} to publish on the website",
                "type": 3,
                "required": True,
                "min_length": 1,
                "max_length": limit,
            }
            for name, limit in _SITE_INPUTS.items()
        ],
    }
    build = {
        "name": _BUILD_SITE,
        "description": "Build or revise a website from a prompt and get a Vercel preview",
        "type": 1,
        "options": [
            {
                "name": "prompt",
                "description": "Describe the site or changes you want",
                "type": 3,
                "required": True,
                "max_length": _BUILD_INPUTS["prompt"],
            },
            {
                "name": "revision",
                "description": "Revision ID from a prior preview, when editing it",
                "type": 3,
                "required": False,
                "max_length": _BUILD_INPUTS["revision"],
            },
        ],
    }
    return [*manifests, flip, connections, launch, build]


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
    binding = matches[0]
    source = DiscordSource(
        guild,
        parent_channel,
        user,
        binding.room_id,
        binding.participant_id,
        document.get("id", ""),
        switch_thread,
        "",
        binding.roles,
    )
    data = document.get("data") or {}
    if not isinstance(data, dict):
        raise ValueError("Discord command data must be an object")
    options = data.get("options") or []
    if data.get("name") == _CONNECTIONS:
        if not isinstance(options, list) or len(options) != 1:
            raise ValueError("An agent selection is required")
        agent = options[0]
        if (
            not isinstance(agent, dict)
            or agent.get("name") != "agent"
            or agent.get("value") not in _CONNECTION_AGENTS
        ):
            raise ValueError("Unsupported agent selection")
        return document, source, _CONNECTIONS, {"agent": agent["value"]}
    if data.get("name") == _LAUNCH_SITE:
        return document, source, _LAUNCH_SITE, _site_args(options, _SITE_INPUTS, _SITE_INPUTS)
    if data.get("name") == _BUILD_SITE:
        args = _site_args(options, _BUILD_INPUTS, ("prompt",))
        if "revision" in args and not re.fullmatch(r"[0-9]{1,30}", args["revision"]):
            raise ValueError("Invalid site revision")
        return document, source, _BUILD_SITE, args
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
    if name in {"ready-for-review", "approve-merge", "merge"}:
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
    body = f"/{data['name']} {name}" + ("" if no_task else f" {values['task_id']}")
    return document, replace(source, body=body), name, values


def _site_args(options, limits, required):
    if not isinstance(options, list) or len(options) > len(limits):
        raise ValueError("Invalid site options")
    result = {}
    for option in options:
        if not isinstance(option, dict):
            raise ValueError("Invalid site option")
        name, value = option.get("name"), option.get("value")
        if (
            not isinstance(name, str)
            or name not in limits
            or name in result
            or not isinstance(value, str)
            or len(value) > limits[name]
            or not value.strip()
            or any(0xD800 <= ord(char) <= 0xDFFF or char == "\x00" for char in value)
        ):
            raise ValueError("Invalid site option")
        result[name] = value.strip()
    if not result.keys() >= set(required):
        raise ValueError("Missing required site option")
    return result


def respond_to_interaction(store, settings, payload: bytes, headers, *, now=None) -> dict:
    _, source, name, values = _resolve(settings, payload, headers, now=now)
    return _respond_to_resolved(store, settings, source, name, values)


def _respond_to_resolved(store, settings, source, name, values):
    if source is None:
        return {"type": 1}
    if name in _MERGE_COMMANDS:
        raise ValueError("Merge commands require the configured asynchronous release service")
    if name in {_CONNECTIONS, _LAUNCH_SITE, _BUILD_SITE}:
        raise ValueError("Command requires the asynchronous Discord handler")
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


async def _agent_connections(settings, source, role):
    if (
        not settings.switch_room_id
        or source.room_id != settings.switch_room_id
        or source.user_id not in settings.discord_connection_viewers.get(role, [])
    ):
        raise PermissionError("Agent connections are unavailable in this scope")

    agent_id = getattr(settings, f"{role}_agent_id")
    api_key = getattr(settings, f"{role}_api_key")
    handoff = (
        "To connect a service, open **Switch Console → Connections** and grant it to the agent."
    )
    if not agent_id or not api_key:
        content = "Connection lookup is unavailable for this agent."
    else:
        client = SwitchClient(settings.switch_base_url, agent_id, api_key.get_secret_value())
        try:
            async with asyncio.timeout(2):
                services = await client.mcp_service_names()
        except (TimeoutError, httpx.HTTPError, SwitchProtocolError, ValueError):
            content = "Couldn't read this agent's MCP service grants from Switch."
        else:
            names = [service for service in services if re.fullmatch(r"[a-z0-9_-]{1,64}", service)]
            if names:
                listed = ", ".join(names[:20])
                suffix = f" (+{len(names) - 20} more)" if len(names) > 20 else ""
                content = f"**{role.upper()} MCP service grants:** {listed}{suffix}"
            else:
                content = f"**{role.upper()} MCP service grants:** none."
        finally:
            await client.close()
    return {
        "type": 4,
        "data": {
            "content": f"{content}\n\n{handoff}\nConsole URI: `switchdash://open/connections`",
            "flags": 64,
            "allowed_mentions": {"parse": []},
        },
    }


async def _merge_result(service, source, name, values):
    if name == "ready-for-review":
        result = await service.mark_ready(values["task_id"], source.user_id, values["revision"])
        return f"PR #{result['pr_number']} is ready for review. Merge approval is still required."
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
    await _edit_interaction_reply(application_id, token, content)


async def _edit_interaction_reply(application_id, token, content):
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.patch(
                f"https://discord.com/api/v10/webhooks/{application_id}/{token}/messages/@original",
                json={"content": content[:1900], "allowed_mentions": {"parse": []}},
            )
    except httpx.HTTPError:
        # Interaction credentials are transient and must not enter exception logs.
        pass


def _interaction_callback(document, background_tasks):
    if background_tasks is None:
        raise ValueError("Deferred Discord acknowledgement is required")
    application_id, token = document.get("application_id"), document.get("token")
    if not isinstance(application_id, str) or not re.fullmatch(r"[0-9]{1,30}", application_id):
        raise ValueError("Discord application identity required")
    if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,500}", token):
        raise ValueError("Discord interaction callback required")
    return application_id, token


async def _finish_site(store, settings, interaction_id, values, application_id, token):
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            url = await deploy_preview(
                client,
                token=settings.vercel_token.get_secret_value(),
                project=settings.vercel_preview_project,
                team_id=settings.vercel_team_id,
                **values,
            )
        content = f"Vercel preview is ready. [Open website]({url})"
    except PreviewError as exc:
        content = str(exc)
    except OSError:
        content = "The website template is unavailable."
    finally:
        store.complete_inbox(interaction_id, _LAUNCH_SITE)
    await _edit_interaction_reply(application_id, token, content)


async def _finish_build(store, settings, source, values, builder, previous, application_id, token):
    revision_id = source.message_id
    try:
        page = await build_site(*builder, values["prompt"], previous)
        async with httpx.AsyncClient(timeout=10) as client:
            url = await deploy_html(
                client,
                token=settings.vercel_token.get_secret_value(),
                project=settings.vercel_preview_project,
                team_id=settings.vercel_team_id,
                page=page,
                generated=True,
            )
        store.save_site_preview(
            revision_id, source.room_id, source.user_id, values.get("revision"), page, url
        )
        content = (
            f"[Open website]({url}) · Revision `{revision_id}`\n"
            "To revise it, run `/build-site` again with this revision ID and your changes."
        )
    except HarnessError:
        content = "The builder couldn't generate a valid page. Check the configured harness."
    except PreviewError as exc:
        content = str(exc)
    finally:
        store.complete_inbox(revision_id, _BUILD_SITE)
    await _edit_interaction_reply(application_id, token, content)


def _queue_site(store, settings, document, source, name, values, background_tasks, builder):
    if (
        not settings.switch_room_id
        or source.room_id != settings.switch_room_id
        or source.user_id not in settings.discord_site_publishers
    ):
        raise PermissionError("Website publishing is unavailable in this scope")
    if not settings.vercel_token or not settings.vercel_preview_project:
        return {
            "type": 4,
            "data": {"flags": 64, "content": "Vercel publishing needs operator setup."},
        }
    previous = None
    if name == _BUILD_SITE:
        if builder is None:
            return {"type": 4, "data": {"flags": 64, "content": "Configure the PM harness first."}}
        if "revision" in values:
            previous = store.site_preview_source(values["revision"], source.room_id, source.user_id)
            if previous is None:
                raise PermissionError("Site revision is unavailable in this scope")
    callback = _interaction_callback(document, background_tasks)
    interaction_id = source.message_id
    if not isinstance(interaction_id, str) or not re.fullmatch(r"[0-9]{1,30}", interaction_id):
        raise ValueError("Discord interaction identity required")
    if not store.receive_inbox(
        interaction_id, name, {"user": source.user_id, "room": source.room_id}
    ):
        return {
            "type": 4,
            "data": {"flags": 64, "content": "This Discord request was already accepted."},
        }
    if name == _BUILD_SITE:
        background_tasks.add_task(
            _finish_build, store, settings, source, values, builder, previous, *callback
        )
    else:
        background_tasks.add_task(_finish_site, store, settings, interaction_id, values, *callback)
    return {"type": 5, "data": {"flags": 64}}


async def respond_to_interaction_async(
    store,
    settings,
    payload,
    headers,
    *,
    merge_service=None,
    site_builder=None,
    background_tasks=None,
    now=None,
):
    from .crew import _task_scope

    document, source, name, values = _resolve(settings, payload, headers, now=now)
    if name == _CONNECTIONS:
        return await _agent_connections(settings, source, values["agent"])
    if name in {_LAUNCH_SITE, _BUILD_SITE}:
        return _queue_site(
            store, settings, document, source, name, values, background_tasks, site_builder
        )
    if source is None or name not in _MERGE_COMMANDS:
        return _respond_to_resolved(store, settings, source, name, values)
    if merge_service is None:
        raise PermissionError("Guarded merge is not configured")
    task = store.get_task(values["task_id"])
    if not task or not _task_scope(task, source):
        raise PermissionError("Merge task is unavailable in this thread")
    if name != "merge-status" and source.user_id not in settings.approval_user_ids:
        raise PermissionError("An explicitly configured Discord human approver is required")
    callback = _interaction_callback(document, background_tasks)
    background_tasks.add_task(_finish_merge, merge_service, source, name, values, *callback)
    return {"type": 5, "data": {"flags": 64}}
