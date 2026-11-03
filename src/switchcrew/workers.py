"""Independent PM and Research processes exchanging durable handoffs via Switch."""

import argparse
import asyncio
import json
import logging
import re
from uuid import NAMESPACE_URL, uuid5

from .approval import ApprovalWorkflow
from .discord import DiscordBinding, normalize_discord_event
from .graphs import build_agent_graph, graph_result, invoke_agent_graph
from .logging import configure_logging
from .orchestration import Orchestrator
from .research import ResearchProvider
from .store import ConflictError, Store
from .switch import SwitchClient, SwitchOwnershipLost

log = logging.getLogger("switchcrew.workers")


def pm_graph(**kwargs):
    return build_agent_graph("pm", **kwargs)


def research_graph(provider, **kwargs):
    return build_agent_graph("research", provider=provider, **kwargs)


async def polling_events(events):
    """Keep durable workflow progress moving while the Switch stream is quiet."""
    iterator = events.__aiter__()
    pending = asyncio.create_task(anext(iterator))
    try:
        while True:
            done, _ = await asyncio.wait({pending}, timeout=2)
            if not done:
                yield {"type": "tick"}
                continue
            try:
                event = pending.result()
            except StopAsyncIteration:
                return
            yield event
            pending = asyncio.create_task(anext(iterator))
    finally:
        pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)


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
        engineering_id="",
        engineering_name="",
        qa_id="",
        reviewer_id="",
        max_corrections=2,
        release_id="",
        release_name="",
        release_repository="",
        release_base="",
        approval_users=(),
        specialist_targets=None,
        orchestration_job_key="",
        incident_channels=(),
        incident_max_events_per_minute=10,
        company_brain=None,
    ):
        if role not in {"pm", "research"} or pm_id == research_id:
            raise ValueError("Distinct PM and Research identities are required")
        self.role, self.store, self.switch = role, store, switch
        self.engineering_id, self.qa_id, self.release_id = engineering_id, qa_id, release_id
        self.reviewer_id, self.max_corrections = reviewer_id, max_corrections
        self.release_repository, self.release_base = release_repository, release_base
        self.approvals = ApprovalWorkflow(store, approval_users)
        self.pm_id, self.research_id, self.room_id = pm_id, research_id, room_id
        self.pm_sender = pm_participant_id or pm_id
        self.research_sender = research_participant_id or research_id
        self.names = {pm_id: pm_name or pm_id, research_id: research_name or research_id}
        if engineering_id:
            self.names[engineering_id] = engineering_name or engineering_id
        if release_id:
            self.names[release_id] = release_name or release_id
        targets = {"research": research_id}
        if engineering_id:
            targets["engineering"] = engineering_id
        for specialist, target in (specialist_targets or {}).items():
            if not isinstance(target, dict) or not target.get("agent_id") or not target.get("name"):
                raise ValueError("Specialist targets need agent_id and exact name")
            targets[specialist] = target["agent_id"]
            self.names[target["agent_id"]] = target["name"]
        self.orchestrator = Orchestrator(
            store,
            pm_id,
            targets,
            engineering_route={
                "qa": qa_id,
                "reviewer": reviewer_id,
                "max_corrections": max_corrections,
                "release": release_id,
                "job_key": orchestration_job_key,
                "repository": release_repository,
                "base": release_base,
            },
            company_brain=company_brain,
        )
        self.identity = pm_id if role == "pm" else research_id
        self.bindings = list(bindings)
        self.allowed_guilds, self.allowed_users = set(allowed_guilds), set(allowed_users)
        from .incidents import IncidentIntake

        self.incidents = IncidentIntake(
            store, incident_channels, max_events_per_minute=incident_max_events_per_minute
        )
        self.graph = build_agent_graph(
            role,
            provider=provider or ResearchProvider([]),
            memory=memory,
            checkpointer=checkpointer,
            company_brain=company_brain,
        )
        self.company_brain = company_brain

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
        if not getattr(self.switch, "connected", True):
            return
        if self.role == "pm":
            for row in self.store.connection.execute("SELECT task_id FROM plans").fetchall():
                data = await self.orchestrator.reconcile_with_context(row[0])
                if data and data["phase"] in {"completed", "blocked"}:
                    self._reply(
                        row[0],
                        "plan_" + data["phase"],
                        "PM orchestration "
                        + data["phase"]
                        + ": "
                        + json.dumps(
                            {
                                key: {
                                    field: step[field]
                                    for field in ("role", "status", "task_id", "reason")
                                    if field in step
                                }
                                for key, step in data["steps"].items()
                            }
                        ),
                    )
            for row in self.store.connection.execute(
                "SELECT task_id FROM approval_workflows"
            ).fetchall():
                task_id = row[0]
                data = self.approvals.reconcile(task_id)
                if (
                    data
                    and data["phase"] == "awaiting_engineering_approval"
                    and data["spec"]["pm"] == self.pm_id
                ):
                    self._reply(
                        task_id,
                        "engineering_approval",
                        f"Engineering request {task_id} awaits human approval. "
                        "Task version: 1. No Engineering action has started.",
                    )
                if data and data["phase"] in {"human_review", "failed"}:
                    task = self.store.get_task(task_id)
                    if data["spec"]["pm"] != self.pm_id or task["status"] not in {
                        "HUMAN_REVIEW",
                        "FAILED",
                    }:
                        continue
                    self._reply(
                        task_id,
                        data["phase"],
                        f"Engineering task {task_id}: {data['phase']}; "
                        f"revision {data.get('revision', 'unavailable')}; "
                        f"task version {task['version']}. "
                        "Review QA evidence before release approval.",
                    )
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
        from .crew import handle_crew_command

        command = handle_crew_command(
            self.store, source, can_cancel=source.user_id in self.approvals.approvers
        )
        if command is not None:
            if command.get("task_id"):
                self._reply(
                    command["task_id"],
                    command["kind"] + "_" + source.message_id,
                    command["text"],
                    freeze=True,
                )
            else:
                await self.switch.post_message(
                    command["text"], thread_id=source.thread_id or source.message_id
                )
            return
        try:
            request = json.loads(source.body)
            if not isinstance(request, dict):
                request = {}
        except ValueError:
            request = {}
        if not request and source.channel_id in self.incidents.allowed_channels:
            incident = self.incidents.ingest(source)
            if incident is not None:
                task_id = incident["task_id"]
                if self.orchestrator.get(task_id) is None:
                    self.orchestrator.start_incident(task_id, incident["incident"])
                await self.orchestrator.reconcile_with_context(task_id)
                task = self.store.get_task(task_id)
                plan = self.orchestrator.get(task_id)
                reason = plan["spec"].get("blocked_reason", "") if plan else ""
                self._reply(
                    task_id,
                    "incident_" + source.message_id,
                    f"Incident {task_id}: {task['status']}. "
                    "Investigation uses configured evidence; code changes require human approval. "
                    + reason,
                    freeze=True,
                )
                return
            # Chatter, rejected reports, and exhausted intake budgets must not
            # fall through into task creation in proactively monitored channels.
            return
        if request.get("type") == "approval":
            try:
                if (
                    not isinstance(request.get("approved"), bool)
                    or not isinstance(request.get("task_id"), str)
                    or type(request.get("expected_version")) is not int
                ):
                    return
                task = self.store.get_task(request.get("task_id", ""))
                if not task or task["source"].get("room_id") != source.room_id:
                    return
                if (task["source"].get("thread_id") or task["source"].get("message_id")) != (
                    source.thread_id or source.message_id
                ):
                    return
                self.approvals.decide(
                    task["id"],
                    request.get("stage"),
                    request["approved"],
                    source.user_id,
                    request.get("expected_version"),
                )
            except (PermissionError, ConflictError, ValueError, TypeError):
                return
            self._reply(task["id"], "decision_" + source.message_id, "Human decision recorded.")
            return
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
                "requested_role": {
                    "engineering": "engineering",
                    "orchestrate": "orchestration",
                }.get(request.get("kind"), "research"),
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
        if request.get("kind") == "orchestrate":
            try:
                self.orchestrator.start(task_id)
                await self.orchestrator.reconcile_with_context(task_id)
            except ValueError:
                self._reply(
                    task_id, "plan_invalid", "Orchestration requires 1–12 bounded criteria."
                )
                return
            self._reply(
                task_id,
                "plan_started",
                f"PM created a durable plan for {task_id}. "
                "Read-only specialists run independently; Engineering requires human approval.",
            )
            return
        if request.get("kind") == "engineering":
            job_key = request.get("job_key")
            if (
                not isinstance(job_key, str)
                or not job_key.strip()
                or not all((self.engineering_id, self.qa_id, self.reviewer_id, self.release_id))
            ):
                self._reply(
                    task_id, "configuration", "Engineering route requires operator configuration."
                )
                return
            existing_approval = self.approvals.get(task_id)
            brain_metadata = (
                existing_approval.get("spec", {}).get("company_brain")
                if existing_approval
                else None
            )
            if self.company_brain is not None and existing_approval is None:
                from .company_brain import capture_engineering_context

                query = question + "\nAcceptance criteria:\n" + "\n".join(criteria)
                brain_metadata = await capture_engineering_context(self.company_brain, query)
            latest = self.store.get_task(task_id)
            existing_approval = self.approvals.get(task_id)
            if not latest or latest["status"] in {
                "CANCELLED",
                "COMPLETED",
                "FAILED",
                "BLOCKED",
            }:
                return
            if latest["status"] != "DRAFT" and existing_approval is None:
                return
            if existing_approval is not None:
                if existing_approval.get("phase") != "awaiting_engineering_approval":
                    return
                brain_metadata = existing_approval.get("spec", {}).get("company_brain")
            try:
                self.approvals.request(
                    task_id,
                    self.pm_id,
                    self.engineering_id,
                    self.qa_id,
                    self.release_id,
                    job_key,
                    self.release_repository,
                    self.release_base,
                    reviewer=self.reviewer_id,
                    max_corrections=self.max_corrections,
                    company_brain=brain_metadata,
                )
            except ConflictError:
                latest = self.store.get_task(task_id)
                if latest and latest["status"] in {
                    "CANCELLED",
                    "COMPLETED",
                    "FAILED",
                    "BLOCKED",
                }:
                    return
                raise
            self._reply(
                task_id,
                "engineering_approval",
                f"Engineering request {task_id} awaits human approval. "
                "Task version: 1. No Engineering action has started.",
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

    def _reply(self, task_id, kind, text, *, freeze=False):
        chunks = [text[i : i + 1600] for i in range(0, len(text), 1600)]
        if freeze:
            # A resumed status/cancel request must reuse its first durable reply,
            # even if the task advanced before the inbox completion committed.
            with self.store._lock, self.store.transaction():
                if self.store.connection.execute(
                    "SELECT 1 FROM outbox WHERE task_id=? AND idempotency_key=?",
                    (task_id, f"{task_id}:{kind}:0"),
                ).fetchone():
                    return
                for index, chunk in enumerate(chunks):
                    self.store.enqueue_outbox(
                        task_id,
                        "discord",
                        {"type": kind, "text": chunk, "worker_sender": self.identity},
                        idempotency_key=f"{task_id}:{kind}:{index}",
                    )
            return
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
            current_task = self.store.get_task(handoff["task_id"])
            parent_id = current_task["source"].get("parent_task_id")
            parent = self.store.get_task(parent_id) if parent_id else None
            if current_task["status"] in {"CANCELLED", "COMPLETED"} or (
                parent_id and (not parent or parent["status"] != "IN_PROGRESS")
            ):
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
            self.store.complete_handoff(
                handoff["id"], self.identity, graph_result(result), execution_role=self.role
            )
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
        if not getattr(self.switch, "connected", True):
            return
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
            parent_id = task["source"].get("parent_task_id")
            parent = self.store.get_task(parent_id) if parent_id else None
            if payload.get("type") == "handoff" and (
                task["status"] in {"CANCELLED", "COMPLETED"}
                or (parent_id and (not parent or parent["status"] != "IN_PROGRESS"))
            ):
                continue
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
        ready = False
        async for event in polling_events(self.switch.events()):
            if event.get("type") == "tick" and (
                not ready or not getattr(self.switch, "connected", True)
            ):
                continue
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
                ready = True
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
    company_brain = None
    if settings.company_brain_enabled:
        from .company_brain import company_brain_from_settings

        company_brain = company_brain_from_settings(settings)
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
                company_brain=company_brain,
                checkpointer=saver,
                engineering_id=settings.engineering_agent_id,
                engineering_name=settings.engineering_name,
                qa_id=settings.qa_agent_id,
                reviewer_id=settings.reviewer_agent_id,
                max_corrections=settings.max_corrections,
                release_id=settings.release_agent_id,
                release_name=settings.release_name,
                release_repository=settings.release_repository,
                release_base=settings.release_base,
                approval_users=settings.approval_user_ids,
                specialist_targets=settings.specialist_targets,
                orchestration_job_key=settings.orchestration_job_key,
                incident_channels=settings.incident_channel_ids,
                incident_max_events_per_minute=settings.incident_max_events_per_minute,
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
