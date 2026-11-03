"""One authenticated Switch connection and one local graph per specialist process."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from .agents import AGENT_ROLES
from .graphs import graph_result, invoke_agent_graph
from .store import Store
from .switch import SwitchClient, SwitchOwnershipLost

log = logging.getLogger("switchcrew.runtime")


def ownership_lost(exc):
    """TaskGroup wraps terminal fencing errors; mixed ordinary failures stay failures."""
    if isinstance(exc, SwitchOwnershipLost):
        return True
    return isinstance(exc, BaseExceptionGroup) and all(ownership_lost(e) for e in exc.exceptions)


@dataclass(frozen=True)
class AgentConnection:
    role: str
    agent_id: str
    participant_id: str
    name: str
    api_key: str = field(repr=False)
    room_id: str = ""
    allowed_senders: dict[str, str] = field(default_factory=dict)
    target_names: dict[str, str] = field(default_factory=dict)

    def __post_init__(self):
        if self.role not in AGENT_ROLES:
            raise ValueError("Unknown specialist role")
        if not all(
            (self.role, self.agent_id, self.participant_id, self.name, self.api_key, self.room_id)
        ):
            raise ValueError("An independent complete Switch identity is required")
        if self.participant_id in self.allowed_senders:
            raise ValueError("Self must not be an allowed sender")
        if self.agent_id in self.allowed_senders.values():
            raise ValueError("Sender identities must be distinct")


class AgentRuntime:
    """Consume addressed durable handoffs; never call another specialist's graph.

    Executors must honor handoff_id as their idempotency key: a crash between
    execution and the result commit can repeat an invocation. Persisted transport
    IDs deduplicate ambiguous Switch POST outcomes on receiving runtimes.
    """

    def __init__(self, connection, store, switch, graph):
        self.connection, self.store, self.switch, self.graph = connection, store, switch, graph
        if getattr(switch, "agent_id", connection.agent_id) != connection.agent_id:
            raise ValueError("Switch client identity differs from runtime identity")
        self.switch.on_disconnect = self.on_disconnected

    def on_disconnected(self):
        """Lifecycle hook for runtimes that perform work outside the event loop."""

    def is_ready(self):
        return getattr(self.switch, "connected", True)

    async def receive(self, event):
        c = self.connection
        if event.get("type") != "message" or event.get("room_id") != c.room_id:
            return False
        p = event.get("payload")
        if not isinstance(p, dict) or p.get("sender_kind") == "platform" or p.get("on_behalf_of"):
            return False
        if not isinstance(p.get("sender"), str):
            return False
        sender = c.allowed_senders.get(p["sender"])
        if sender is None or not isinstance(p.get("message_id"), str) or not p["message_id"]:
            return False
        try:
            body = p["body"]
            marker = re.search(r"```switchcrew\n(.*?)\n```\s*$", body, re.DOTALL)
            message = json.loads(marker.group(1) if marker else body)
            if not isinstance(message, dict):
                return False
            if not isinstance(message.get("handoff_id"), str) or not message["handoff_id"]:
                return False
            handoff = self.store.get_handoff(message["handoff_id"])
            if not handoff or message.get("task_id") != handoff["task_id"]:
                return False
            kind = message.get("type")
            if kind == "handoff":
                if handoff["sender"] != sender or handoff["recipient"] != c.agent_id:
                    return False
                for key in ("question", "acceptance_criteria"):
                    if key in message and message[key] != handoff[key]:
                        return False
            elif kind in {"handoff.accepted", "handoff.completed"}:
                if handoff["recipient"] != sender or handoff["sender"] != c.agent_id:
                    return False
                if kind == "handoff.completed" and handoff["status"] != "completed":
                    return False
            else:
                return False
            identity = message.get("delivery_id", p["message_id"])
            if not isinstance(identity, str) or not identity:
                return False
        except (KeyError, TypeError, ValueError):
            return False
        canonical = {"sender": sender, "message": message}
        return self.store.receive_inbox(identity, c.agent_id, canonical, handoff["task_id"])

    async def process_pending(self):
        c = self.connection
        for item in self.store.pending_inbox(c.agent_id):
            if not self.is_ready():
                return
            message = item["payload"]["message"]
            handoff = self.store.get_handoff(message["handoff_id"])
            if message["type"] == "handoff" and handoff["status"] != "completed":
                self.store.accept_handoff(handoff["id"], c.agent_id)
                await self.flush_outbox()  # Durable acceptance must be delivered before work.
                if not self.is_ready():
                    return
                task = self.store.get_task(handoff["task_id"])
                parent_id = task["source"].get("parent_task_id")
                parent = self.store.get_task(parent_id) if parent_id else None
                if task["status"] in {"CANCELLED", "COMPLETED"} or (
                    parent_id and (not parent or parent["status"] != "IN_PROGRESS")
                ):
                    self.store.complete_handoff(
                        handoff["id"],
                        c.agent_id,
                        {
                            "status": "blocked",
                            "reason": "task_terminal",
                            "task_status": task["status"],
                            "role": c.role,
                        },
                    )
                else:
                    state = {
                        "question": handoff["question"],
                        "acceptance_criteria": handoff["acceptance_criteria"],
                        "task_id": handoff["task_id"],
                        "handoff_id": handoff["id"],
                        "session_id": handoff["task_id"],
                    }
                    state["agent_role"] = c.role
                    result = await invoke_agent_graph(self.graph, state)
                    completed = self.store.complete_handoff(
                        handoff["id"], c.agent_id, graph_result(result), c.role
                    )
                    if completed is None:
                        continue
            self.store.complete_inbox(item["message_id"], c.agent_id)

    async def flush_outbox(self):
        if not self.is_ready():
            return
        c = self.connection
        for item in self.store.pending_outbox():
            if not self.is_ready():
                return
            p = item["payload"]
            if not p.get("handoff_id"):
                continue
            h = self.store.get_handoff(p["handoff_id"])
            owner = h["sender"] if p["type"] == "handoff" else h["recipient"]
            if owner != c.agent_id:
                continue
            task = self.store.get_task(item["task_id"])
            parent_id = task["source"].get("parent_task_id")
            parent = self.store.get_task(parent_id) if parent_id else None
            if p["type"] == "handoff" and (
                task["status"] in {"CANCELLED", "COMPLETED"}
                or (parent_id and (not parent or parent["status"] != "IN_PROGRESS"))
            ):
                continue
            target = c.target_names.get(item["recipient"])
            if not target:
                raise ValueError("Missing target name for durable delivery")
            source = task["source"]
            body = (
                "```switchcrew\n"
                + json.dumps(
                    {**p, "task_id": item["task_id"], "delivery_id": item["id"]}, sort_keys=True
                )
                + "\n```"
            )
            result = await self.switch.send_targeted_message(
                body,
                target_names=[target],
                thread_id=source.get("thread_id") or source.get("message_id"),
            )
            if result.get("target_statuses", {}).get(target) != "live":
                raise RuntimeError("Addressed Switch target is not live")
            self.store.mark_outbox_sent(item["id"], result.get("event_id"))

    async def _recover(self, event):
        for page in reversed(event.get("history", {}).get(self.connection.room_id, [])):
            for group in page.get("threads", []):
                root = group["root"]
                for entry in [root, *group.get("replies", [])]:
                    if entry.get("kind", "message") == "message":
                        await self.receive(
                            {
                                "type": "message",
                                "room_id": self.connection.room_id,
                                "payload": {**entry, "message_id": entry["id"]},
                            }
                        )

    async def on_connected(self):
        await self.switch.connect_to_room(self.connection.room_id)

    async def run(self):
        identity = self.connection.agent_id
        cursor = self.store.get_cursor(identity)
        self.switch.cursor = int(cursor) if cursor is not None else None
        # A restart can have durable work even if the stream has no new messages.
        from .workers import polling_events

        async for event in polling_events(self.switch.events()):
            if event.get("type") == "connection_state":
                await self.on_connected()
            elif event.get("type") == "gap":
                await self._recover(event)
            else:
                await self.receive(event)
            sequence = (
                event.get("resumed_at") if event.get("type") == "gap" else event.get("sequence")
            )
            if sequence is not None:
                self.store.set_cursor(identity, sequence)
                await self.switch.acknowledge(sequence)
            await self.process_pending()
            await self.flush_outbox()


def load_connection(path, role):
    """Load one process's credentials; its peers are public identity mappings only."""
    connection = AgentConnection(**json.loads(Path(path).read_text()))
    if connection.role != role:
        raise ValueError("Registry role differs from requested role")
    return connection


async def _main(role, registry, database, url, capability_config=None):
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    from .graphs import build_agent_graph
    from .memory import memory_from_settings
    from .research import ResearchProvider
    from .settings import Settings

    if capability_config and Path(registry).stat().st_mode & 0o077:
        raise ValueError("Capability runtime registry must be private")
    c = load_connection(registry, role)
    private_config = {}
    if capability_config:
        path = Path(capability_config)
        if path.stat().st_mode & 0o077:
            raise ValueError("Capability configuration must be private")
        private_config = json.loads(path.read_text())
        if private_config.get("role") != role:
            raise ValueError("Capability configuration role differs from registry")
    settings = Settings(
        **({"_env_file": None, **private_config.get("memory", {})} if capability_config else {}),
        **({"switch_base_url": url} if url else {}),
    )
    database = database or settings.database_path
    if (
        capability_config
        and role == "voice"
        and (Path(private_config["ledger"]).resolve() == Path(database).resolve())
    ):
        raise ValueError("Application state and Voice execution ledger require separate files")
    settings.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    store = Store(database)
    client = SwitchClient(
        settings.switch_base_url,
        c.agent_id,
        c.api_key,
        connection_id=str(uuid5(NAMESPACE_URL, f"switchcrew:{c.role}:{c.agent_id}")),
        rooms=[c.room_id],
    )
    executor, execution_ledger = None, None
    try:
        if capability_config:
            from .specialists import SPECIALIST_ROLES, specialist_executor

            if role in SPECIALIST_ROLES:
                executor = specialist_executor(role, private_config["capability"])
            elif role == "voice":
                from .engineering import Ledger
                from .voice import voice_executor_from_config

                execution_ledger = Ledger(private_config["ledger"])

                def voice_authorized(state):
                    task = store.get_task(state["task_id"])
                    if not task or task["status"] not in {"IN_PROGRESS", "QA_REVIEW"}:
                        raise PermissionError("Voice task is not active")
                    parent_id = task["source"].get("parent_task_id")
                    if parent_id and not (
                        (parent := store.get_task(parent_id)) and parent["status"] == "IN_PROGRESS"
                    ):
                        raise PermissionError("Voice parent task is not active")

                executor = voice_executor_from_config(
                    private_config["capability"], execution_ledger, authorize=voice_authorized
                )
            else:
                raise ValueError(
                    "Use the approval-gated entry point for privileged Engineering/QA/Release"
                )
        async with AsyncSqliteSaver.from_conn_string(str(settings.checkpoint_path)) as saver:
            graph = build_agent_graph(
                role,
                provider=ResearchProvider(
                    settings.research_source_urls, settings.research_max_sources
                ),
                memory=memory_from_settings(settings),
                executor=executor,
                checkpointer=None if role == "voice" else saver,
            )
            await AgentRuntime(c, store, client, graph).run()
    finally:
        await client.close()
        store.close()
        if execution_ledger is not None:
            execution_ledger.close()


async def run_runtime(role, registry, database, url, capability_config=None):
    try:
        if capability_config:
            await _main(role, registry, database, url, capability_config)
        else:
            await _main(role, registry, database, url)
    except Exception as exc:
        terminal = ownership_lost(exc)
        log.error(
            "runtime_ownership_lost" if terminal else "runtime_failed",
            extra={"role": role, "error_type": type(exc).__name__},
        )
        return 0 if terminal else 1
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("role", choices=AGENT_ROLES)
    parser.add_argument(
        "--registry", default=os.getenv("SWITCHCREW_AGENT_REGISTRY"), required=False
    )
    parser.add_argument("--database")
    parser.add_argument("--url")
    parser.add_argument(
        "--capability-config", help="Private operator-owned specialist/voice config"
    )
    args = parser.parse_args()
    if not args.registry:
        parser.error("--registry or SWITCHCREW_AGENT_REGISTRY is required")
    from .logging import configure_logging

    configure_logging()
    raise SystemExit(
        asyncio.run(
            run_runtime(args.role, args.registry, args.database, args.url, args.capability_config)
        )
    )
