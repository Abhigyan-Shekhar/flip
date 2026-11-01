"""Independent PM and Research processes exchanging durable handoffs via Switch."""

import argparse
import asyncio
import json
import logging
import re
from uuid import NAMESPACE_URL, uuid5

from .discord import DiscordBinding, normalize_discord_event
from .graphs import build_agent_graph, graph_result, invoke_agent_graph
from .logging import configure_logging
from .research import ResearchProvider
from .store import Store
from .switch import SwitchClient, SwitchOwnershipLost

log = logging.getLogger("switchcrew.workers")


def pm_graph(**kwargs):
    return build_agent_graph("pm", **kwargs)


def research_graph(provider, **kwargs):
    return build_agent_graph("research", provider=provider, **kwargs)


class Worker:
    def __init__(
        self,
        role,
        store,
        switch,
        *,
        pm_id,
        research_id,
        room_id,
        bindings=(),
        allowed_guilds=(),
        allowed_users=(),
        provider=None,
        pm_participant_id=None,
        research_participant_id=None,
        pm_name=None,
        research_name=None,
        memory=None,
        checkpointer=None,
    ):
        if role not in {"pm", "research"} or pm_id == research_id:
            raise ValueError("Distinct PM and Research identities are required")
        self.role, self.store, self.switch = role, store, switch
        self.pm_id, self.research_id, self.room_id = pm_id, research_id, room_id
        self.pm_sender = pm_participant_id or pm_id
        self.research_sender = research_participant_id or research_id
        self.names = {pm_id: pm_name or pm_id, research_id: research_name or research_id}
        self.identity = pm_id if role == "pm" else research_id
        self.bindings = list(bindings)
        self.allowed_guilds, self.allowed_users = set(allowed_guilds), set(allowed_users)
        self.graph = build_agent_graph(
            role,
            provider=provider or ResearchProvider([]),
            memory=memory,
            checkpointer=checkpointer,
        )

    async def receive(self, event):
        if event.get("type") != "message" or event.get("room_id") != self.room_id:
            return
        payload = event.get("payload", {})
        if not isinstance(payload, dict):
            return
        if payload.get("sender") == (self.pm_sender if self.role == "pm" else self.research_sender):
            return
        message_id = payload.get("message_id")
        if not isinstance(message_id, str) or not message_id:
            return
        if self.role == "pm" and payload.get("sender") != self.research_sender:
            try:
                normalize_discord_event(
                    event,
                    self.bindings,
                    allowed_guilds=self.allowed_guilds,
                    allowed_users=self.allowed_users,
                )
            except ValueError:
                return
        elif payload.get("sender") != (
            self.pm_sender if self.role == "research" else self.research_sender
        ):
            return
        if payload.get("sender_kind") == "platform" or payload.get("on_behalf_of"):
            return
        if payload.get("sender") in {self.pm_sender, self.research_sender}:
            try:
                body = payload["body"]
                marker = re.search(r"```switchcrew\n(.*?)\n```\s*$", body, re.DOTALL)
                message = json.loads(marker.group(1) if marker else body)
                if isinstance(message.get("delivery_id"), str):
                    message_id = message["delivery_id"]
                    event = {
                        "type": "message",
                        "room_id": self.room_id,
                        "payload": {
                            "sender": payload["sender"],
                            "message_id": message_id,
                            "body": json.dumps(message, sort_keys=True),
                            "thread_id": payload.get("thread_id"),
                        },
                    }
            except (KeyError, TypeError, ValueError, AttributeError):
                return
        # Stream sequences and transport metadata can change across replay.
        # Compare only stable, authenticated message content in the inbox.
        canonical = {
            "type": "message",
            "room_id": self.room_id,
            "bridge_id": event.get("bridge_id"),
            "payload": {
                key: event["payload"].get(key)
                for key in ("sender", "message_id", "body", "thread_id")
            },
        }
        self.store.receive_inbox(message_id, self.identity, canonical)

    async def process_pending(self):
        for item in self.store.pending_inbox(self.identity):
            event = item["payload"]
            if self.role == "pm" and event["payload"]["sender"] != self.research_sender:
                await self._request(event)
            else:
                await self._handoff(event)
            self.store.complete_inbox(item["message_id"], self.identity)

    async def _request(self, event):
        source = normalize_discord_event(
            event,
            self.bindings,
            allowed_guilds=self.allowed_guilds,
            allowed_users=self.allowed_users,
        )
        try:
            request = json.loads(source.body)
            if not isinstance(request, dict):
                request = {}
        except ValueError:
            request = {}
        question = request.get("question", source.body)
        criteria = request.get("acceptance_criteria", [])
        if (
            not isinstance(question, str)
            or not isinstance(criteria, list)
            or any(not isinstance(c, str) for c in criteria)
        ):
            question, criteria = source.body, []
        if not question.strip():
            question, criteria = source.body.strip() or "Missing research question", []
        task_id = str(uuid5(NAMESPACE_URL, f"switchcrew:{source.room_id}:{source.message_id}"))
        task = self.store.create_task(
            question,
            {
                "room_id": source.room_id,
                "thread_id": source.thread_id,
                "message_id": source.message_id,
                "user_id": source.user_id,
            },
            criteria,
            task_id=task_id,
        )
        state = await invoke_agent_graph(
            self.graph,
            {
                "agent_role": "pm",
                "task_id": task_id,
                "handoff_id": "",
                "session_id": task_id,
                "question": question,
                "acceptance_criteria": criteria,
            },
        )
        valid = state["valid"]
        if not valid:
            if task["status"] == "DRAFT":
                self.store.transition_task(task_id, "NEEDS_CLARIFICATION", task["version"])
            self._reply(
                task_id,
                "clarification",
                (
                    "Send a new JSON request with question and nonempty acceptance_criteria. "
                    "This task remains awaiting clarification."
                ),
            )
            return
        handoff_id = str(uuid5(NAMESPACE_URL, f"{task_id}:research"))
        for current, next_status in [
            ("DRAFT", "PLANNED"),
            ("PLANNED", "IN_PROGRESS"),
        ]:
            if task["status"] == current:
                task = self.store.transition_task(
                    task_id, next_status, task["version"], details={"scope": "read_only_research"}
                )
        if not self.store.get_handoff(handoff_id):
            self.store.create_handoff(
                task_id,
                self.pm_id,
                self.research_id,
                question,
                criteria,
                expected_version=task["version"],
                handoff_id=handoff_id,
            )
        self._reply(
            task_id,
            "delegated",
            "PM delegated the question and criteria to Research; awaiting acknowledgment.",
        )

    def _reply(self, task_id, kind, text):
        chunks = [text[i : i + 1600] for i in range(0, len(text), 1600)]
        for index, chunk in enumerate(chunks):
            self.store.enqueue_outbox(
                task_id,
                "discord",
                {"type": kind, "text": chunk, "worker_sender": self.identity},
                idempotency_key=f"{task_id}:{kind}:{index}",
            )

    async def _handoff(self, event):
        try:
            body = event["payload"]["body"]
            marker = re.search(r"```switchcrew\n(.*?)\n```\s*$", body, re.DOTALL)
            message = json.loads(marker.group(1) if marker else body)
            if not isinstance(message, dict):
                return
            if not isinstance(message.get("handoff_id"), str) or not message["handoff_id"]:
                return
            handoff = self.store.get_handoff(message["handoff_id"])
        except (ValueError, KeyError, TypeError):
            return
        if not handoff:
            return
        if message.get("task_id") != handoff["task_id"]:
            return
        if self.role == "research":
            if (
                message.get("type") != "handoff"
                or handoff["sender"] != self.pm_id
                or handoff["recipient"] != self.identity
            ):
                return
            if handoff["status"] == "completed":
                return
            self.store.accept_handoff(handoff["id"], self.identity)
            await self.flush_outbox()  # Explicit acceptance precedes evidence retrieval.
            if self.store.get_task(handoff["task_id"])["status"] in {"CANCELLED", "COMPLETED"}:
                self.store.complete_handoff(
                    handoff["id"],
                    self.identity,
                    {
                        "status": "blocked",
                        "reason": "Parent task is terminal",
                    },
                )
                return
            result = await invoke_agent_graph(
                self.graph,
                {
                    "agent_role": "research",
                    "task_id": handoff["task_id"],
                    "handoff_id": handoff["id"],
                    "session_id": handoff["task_id"],
                    "question": handoff["question"],
                    "acceptance_criteria": handoff["acceptance_criteria"],
                },
            )
            self.store.complete_handoff(handoff["id"], self.identity, graph_result(result))
        elif handoff["recipient"] == self.research_id and handoff["sender"] == self.identity:
            if message.get("type") == "handoff.accepted" and handoff["status"] in {
                "accepted",
                "completed",
            }:
                self._reply(
                    handoff["task_id"],
                    "research_accepted",
                    "Research explicitly acknowledged the handoff.",
                )
            elif message.get("type") == "handoff.completed" and handoff["status"] == "completed":
                task = self.store.get_task(handoff["task_id"])
                if task["status"] == "IN_PROGRESS":
                    self.store.transition_task(
                        task["id"],
                        "COMPLETED"
                        if handoff["result"]["status"] == "evidence_found"
                        else "BLOCKED",
                        task["version"],
                    )
                self._reply(
                    handoff["task_id"],
                    "research_result",
                    "Research result: " + json.dumps(handoff["result"], ensure_ascii=False),
                )

    async def flush_outbox(self):
        for item in self.store.pending_outbox():
            payload = item["payload"]
            handoff = (
                self.store.get_handoff(payload["handoff_id"]) if payload.get("handoff_id") else None
            )
            owner = (
                (handoff["sender"] if payload["type"] == "handoff" else handoff["recipient"])
                if handoff
                else payload.get("worker_sender")
            )
            if owner != self.identity:
                continue
            task = self.store.get_task(item["task_id"])
            thread = task["source"].get("thread_id") or task["source"].get("message_id")
            # Persisted logical delivery ID allows receiving workers to deduplicate
            # ambiguous transport outcomes; Switch itself has no POST idempotency API.
            body = (
                "```switchcrew\n"
                + json.dumps({**payload, "task_id": item["task_id"], "delivery_id": item["id"]})
                + "\n```"
            )
            if item["recipient"] == "discord":
                result = await self.switch.post_message(payload["text"], thread_id=thread)
            else:
                result = await self.switch.send_targeted_message(
                    body, target_names=[self.names[item["recipient"]]], thread_id=thread
                )
            if item["recipient"] != "discord":
                statuses = result.get("target_statuses", {})
                if statuses.get(self.names[item["recipient"]]) != "live":
                    raise RuntimeError(f"Target is not live: {statuses}")
            external_id = (
                result.get("event_id", result.get("message_id"))
                if isinstance(result, dict)
                else None
            )
            self.store.mark_outbox_sent(item["id"], external_id)

    async def run(self):
        self.switch.cursor = (
            int(self.store.get_cursor(self.identity))
            if self.store.get_cursor(self.identity)
            else None
        )
        async for event in self.switch.events():
            if event.get("type") == "connection_state":
                log.info(
                    "worker_connected",
                    extra={
                        "role": self.role,
                        "connection_id": self.switch.connection_id,
                        "generation": event.get("generation"),
                        "cursor": self.switch.cursor,
                    },
                )
                await self.switch.connect_to_room(self.room_id)
                # Role leases belong to the live session. Re-establish them on
                # reconnect as well as startup; release is idempotent.
                await self.switch.operation("release_role")
                await self.switch.operation("assume_role", role=self.role)
                log.info("worker_role_assumed", extra={"role": self.role})
                await self.flush_outbox()
            if event.get("type") == "gap":
                log.info("history_gap_received", extra={"role": self.role})
                for room, pages in event["history"].items():
                    if room != self.room_id:
                        continue
                    for page in reversed(pages):
                        for group in page.get("threads", []):
                            root = group["root"]
                            for entry in [root, *group.get("replies", [])]:
                                if entry.get("kind", "message") != "message":
                                    continue
                                bridge_id = None
                                if entry.get("sender") not in {
                                    self.pm_sender,
                                    self.research_sender,
                                }:
                                    matches = [
                                        binding
                                        for binding in self.bindings
                                        if binding.room_id == room
                                        and binding.participant_id == entry.get("sender")
                                    ]
                                    if len(matches) != 1:
                                        continue
                                    bridge_id = matches[0].bridge_id
                                await self.receive(
                                    {
                                        "type": "message",
                                        "room_id": room,
                                        "bridge_id": bridge_id,
                                        "payload": {
                                            **entry,
                                            "sender": entry["sender"],
                                            "body": entry["body"],
                                            "message_id": entry["id"],
                                            "thread_id": None if entry is root else root["id"],
                                        },
                                    }
                                )
                self.store.set_cursor(self.identity, event["resumed_at"])
                log.info(
                    "history_gap_replayed",
                    extra={"role": self.role, "cursor": event["resumed_at"]},
                )
            await self.receive(event)
            if event.get("sequence") is not None:
                self.store.set_cursor(self.identity, event["sequence"])
                await self.switch.acknowledge(event["sequence"])
            await self.process_pending()
            await self.flush_outbox()


async def _main(role):
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    from .memory import memory_from_settings
    from .settings import Settings

    settings = Settings()
    key = settings.pm_api_key if role == "pm" else settings.research_api_key
    if key is None or not key.get_secret_value():
        raise ValueError(f"{role} API key is required")
    if not all(
        (
            settings.pm_agent_id,
            settings.research_agent_id,
            settings.switch_room_id,
            settings.pm_participant_id,
            settings.research_participant_id,
        )
    ):
        raise ValueError("Configure room, both registration IDs and both participant IDs")
    if settings.pm_participant_id == settings.research_participant_id:
        raise ValueError("Specialist participant IDs must be distinct")
    identity = settings.pm_agent_id if role == "pm" else settings.research_agent_id
    client = SwitchClient(
        settings.switch_base_url,
        identity,
        key.get_secret_value(),
        connection_id=str(uuid5(NAMESPACE_URL, f"switchcrew:{identity}:worker")),
        rooms=[settings.switch_room_id],
    )
    store = Store(settings.database_path)
    settings.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        async with AsyncSqliteSaver.from_conn_string(str(settings.checkpoint_path)) as saver:
            worker = Worker(
                role,
                store,
                client,
                pm_id=settings.pm_agent_id,
                research_id=settings.research_agent_id,
                room_id=settings.switch_room_id,
                bindings=[DiscordBinding(**b) for b in settings.discord_bindings],
                allowed_guilds=settings.discord_guild_ids,
                allowed_users=settings.discord_user_ids,
                provider=ResearchProvider(
                    settings.research_source_urls, settings.research_max_sources
                ),
                pm_participant_id=settings.pm_participant_id,
                research_participant_id=settings.research_participant_id,
                pm_name=settings.pm_name,
                research_name=settings.research_name,
                memory=memory_from_settings(settings),
                checkpointer=saver,
            )
            await worker.run()
    finally:
        await client.close()
        store.close()


async def run_worker(role):
    """Return an exit status for an on-failure supervisor without leaking errors."""
    try:
        await _main(role)
    except SwitchOwnershipLost:
        log.error("worker_ownership_lost", extra={"role": role})
        return 0  # The recovery Compose override restarts failures, not displaced holders.
    except Exception as exc:
        log.error("worker_failed", extra={"role": role, "error_type": type(exc).__name__})
        return 1
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("role", choices=["pm", "research"])
    configure_logging()
    raise SystemExit(asyncio.run(run_worker(parser.parse_args().role)))
