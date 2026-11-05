"""Independent PM and Research processes exchanging durable handoffs via Switch."""

import argparse
import asyncio
import json
import logging
import re
from uuid import NAMESPACE_URL, uuid5

from .approval import ApprovalWorkflow
from .conversation import decision_phrase, human_request
from .discord import DiscordBinding, normalize_discord_event
from .graphs import build_agent_graph, graph_result, invoke_agent_graph
from .harnesses import HarnessError
from .logging import configure_logging
from .orchestration import Orchestrator
from .research import ResearchProvider
from .store import ConflictError, Store
from .switch import SwitchClient, SwitchOwnershipLost
from .wire import plan_summary, read_message, render_message, research_summary

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
        engineering_job_key="",
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
        project_id="github.com/Abhigyan-Shekhar/flip",
        harness_roles=None,
        harness_runner=None,
        planner_policy=None,
    ):
        if role not in {"pm", "research"} or pm_id == research_id:
            raise ValueError("Distinct PM and Research identities are required")
        self.role, self.store, self.switch = role, store, switch
        self.engineering_job_key = engineering_job_key
        self.project_id = project_id
        self.meetings = None
        self.security_gate = None
        self.security_scans = {}
        self.green_check_routes = {}
        self.documentation_id = (specialist_targets or {}).get("documentation", {}).get("agent_id")
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
        self.specialist_senders = {}
        if engineering_id:
            targets["engineering"] = engineering_id
        for specialist, target in (specialist_targets or {}).items():
            if not isinstance(target, dict) or not target.get("agent_id") or not target.get("name"):
                raise ValueError("Specialist targets need agent_id and exact name")
            targets[specialist] = target["agent_id"]
            self.names[target["agent_id"]] = target["name"]
            sender = target.get("participant_id") or target["agent_id"]
            if (
                sender in {self.pm_sender, self.research_sender}
                or sender in self.specialist_senders
            ):
                raise ValueError("Specialist participant identities must be distinct")
            self.specialist_senders[sender] = target["agent_id"]
        from .harnesses import HarnessExecutor, HarnessPlanner

        harness_roles = harness_roles or {}
        planner_policy = planner_policy or {}
        planner = (
            HarnessPlanner(
                harness_roles["pm"],
                runner=harness_runner,
                available_roles=set(harness_roles),
                **planner_policy,
            )
            if role == "pm" and "pm" in harness_roles
            else None
        )
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
            planner=planner,
            reviewers=approval_users,
        )
        self.identity = pm_id if role == "pm" else research_id
        self.bindings = list(bindings)
        self.allowed_guilds, self.allowed_users = set(allowed_guilds), set(allowed_users)
        from .incidents import IncidentIntake

        self.incidents = IncidentIntake(
            store,
            incident_channels,
            max_events_per_minute=incident_max_events_per_minute,
            project_id=self.project_id,
        )
        self.graph = build_agent_graph(
            role,
            provider=provider or ResearchProvider([]),
            executor=(
                HarnessExecutor(
                    harness_roles["research"],
                    runner=harness_runner,
                    source_provider=provider or ResearchProvider([]),
                    company_brain=company_brain,
                )
                if role == "research" and "research" in harness_roles
                else None
            ),
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
        if (
            self.role == "pm"
            and payload.get("sender") != self.research_sender
            and payload.get("sender") not in self.specialist_senders
        ):
            try:
                normalize_discord_event(
                    event,
                    self.bindings,
                    allowed_guilds=self.allowed_guilds,
                    allowed_users=self.allowed_users,
                )
            except ValueError:
                return
        elif payload.get("sender") not in self.specialist_senders and payload.get("sender") != (
            self.pm_sender if self.role == "research" else self.research_sender
        ):
            return
        if payload.get("sender_kind") == "platform" or payload.get("on_behalf_of"):
            return
        if payload.get("sender") in {self.pm_sender, self.research_sender} | set(
            self.specialist_senders
        ):
            try:
                body = payload["body"]
                message = read_message(body, self.store)
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
        from .watchdog import record_worker_heartbeat

        record_worker_heartbeat(self.store, self.identity)
        if self.role == "pm":
            for row in self.store.connection.execute("SELECT task_id FROM plans").fetchall():
                data = await self.orchestrator.reconcile_with_context(row[0])
                if data and data["phase"] == "running":
                    parent = self.store.get_task(row[0])
                    for step_id, step in data["steps"].items():
                        if step.get("status") != "awaiting_review":
                            continue
                        summary = research_summary(step.get("result", {}))
                        self._reply(
                            row[0],
                            f"draft_review_{step_id}_v{parent['version']}",
                            f"{step['role'].capitalize()} finished {step_id}. "
                            "Please review this draft before it is used by the next specialist.\n\n"
                            + summary[:1200]
                            + f"\n\nTo accept this draft, reply:\nApprove draft for task {row[0]} "
                            + f"step {step_id} version {parent['version']}\n"
                            + "This does not authorize Engineering execution or publication.",
                            freeze=True,
                        )
                if data and data["phase"] in {"completed", "blocked"}:
                    self._reply(
                        row[0],
                        "plan_" + data["phase"],
                        plan_summary(data),
                        freeze=True,
                    )
            for row in self.store.connection.execute(
                "SELECT task_id FROM approval_workflows"
            ).fetchall():
                task_id = row[0]
                managed = self.approvals.get(task_id)
                if not managed or managed.get("spec", {}).get("pm") != self.pm_id:
                    continue
                data = self.approvals.reconcile(task_id)
                if (
                    data
                    and self.security_gate
                    and data["spec"]["pm"] == self.pm_id
                    and data.get("phase") in {"qa", "review", "human_review", "release"}
                    and data.get("revision")
                ):
                    from .security_gate import schedule_security_gate

                    try:
                        schedule_security_gate(
                            self.store,
                            task_id,
                            self.pm_id,
                            self.security_gate["security_id"],
                            self.security_gate["policy"],
                        )
                    except (ValueError, PermissionError, OSError):
                        current = self.store.get_task(task_id)
                        if not self.store.connection.execute(
                            "SELECT 1 FROM events WHERE task_id=? AND type=? AND version=?",
                            (task_id, "security.gate_unavailable", current["version"]),
                        ).fetchone():
                            self.store._event(
                                task_id,
                                "security.gate_unavailable",
                                {"reason": "configured_security_policy_or_checkout_unavailable"},
                                current["version"],
                            )

                if (
                    data
                    and data["phase"] == "awaiting_engineering_approval"
                    and data["spec"]["pm"] == self.pm_id
                ):
                    self._engineering_prompt(task_id)
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
                        (
                            "Engineering's changes passed QA and independent review. "
                            f"Revision: {data.get('revision', 'unavailable')}.\n"
                            "Review the evidence, then authorize the draft PR by replying:\n"
                            + (
                                f"/flip approve-release task_id:{task_id} "
                                f"version:{task['version']} revision:{data['revision']}"
                                if task["source"].get("investigation_task_id")
                                else decision_phrase(task_id, "release", task["version"])
                            )
                            if data["phase"] == "human_review"
                            else "The checks haven't passed. This needs attention before release."
                        ),
                        freeze=True,
                    )
                if data and self.documentation_id and data["spec"]["pm"] == self.pm_id:
                    from .organization_runtime import schedule_documentation

                    schedule_documentation(self.store, task_id, self.pm_id, self.documentation_id)
            self._reconcile_auxiliary()
            if self.meetings is not None:
                for row in self.store.connection.execute(
                    "SELECT id FROM meetings WHERE status='actions_confirmed' "
                    "AND json_extract(source,'$.room_id')=? LIMIT 100",
                    (self.room_id,),
                ).fetchall():
                    self.meetings.reconcile_actions(row[0])
        for item in self.store.pending_inbox(self.identity):
            event = item["payload"]
            if (
                self.role == "pm"
                and event["payload"]["sender"] != self.research_sender
                and event["payload"]["sender"] not in self.specialist_senders
            ):
                await self._request(event)
            else:
                await self._handoff(event)
            self.store.complete_inbox(item["message_id"], self.identity)

    async def _meeting_request(self, source, request):
        """Verified Discord control of the meeting workflow; no identity from message JSON."""
        receipt_id = str(
            uuid5(NAMESPACE_URL, f"meeting-command:{source.room_id}:{source.message_id}")
        )
        self.store.create_task(
            "Meeting command receipt",
            {
                "room_id": source.room_id,
                "thread_id": source.thread_id,
                "message_id": source.message_id,
                "user_id": source.user_id,
                "channel_id": source.channel_id,
                "guild_id": source.guild_id,
                "command_receipt": True,
            },
            [],
            task_id=receipt_id,
        )
        text = "Meeting workflow is not configured."
        if self.meetings is not None:
            try:
                meeting_id, operation = request.get("meeting_id"), request.get("operation")
                if operation == "create":
                    self.meetings.create_meeting(
                        meeting_id,
                        source.channel_id,
                        source.thread_id or source.message_id,
                        source.user_id,
                        request.get("participants"),
                        source={
                            "room_id": source.room_id,
                            "guild_id": source.guild_id,
                            "channel_id": source.channel_id,
                            "message_id": source.message_id,
                            "user_id": source.user_id,
                            "project_id": self.project_id,
                        },
                    )
                    result = {
                        "meeting_id": meeting_id,
                        "disclosure": "Recording and transcription require every participant's "
                        "explicit revocable consent.",
                    }
                else:
                    meeting = self.meetings._meeting(meeting_id)
                    if (
                        meeting["source"].get("room_id") != source.room_id
                        or meeting["channel_id"] != source.channel_id
                        or meeting["thread_id"] != (source.thread_id or source.message_id)
                    ):
                        raise PermissionError("Original meeting thread required")
                    if operation == "consent":
                        result = self.meetings.grant_consent(
                            meeting_id,
                            source.user_id,
                            source.user_id,
                            evidence_id=source.message_id,
                            expires_at=request.get("expires_at"),
                            disclosure_acknowledged=request.get("disclosure_acknowledged"),
                        )
                    elif operation == "revoke":
                        result = self.meetings.revoke_consent(
                            meeting_id, source.user_id, source.user_id
                        )
                    elif operation == "cancel":
                        result = self.meetings.cancel_meeting(meeting_id, source.user_id)
                    elif operation == "notes":
                        result = self.meetings.request_notes(meeting_id, source.user_id)
                    elif operation == "confirm":
                        result = self.meetings.confirm_actions(
                            meeting_id,
                            request.get("notes_handoff_id"),
                            request.get("actions"),
                            source.user_id,
                        )
                    else:
                        raise ValueError("Unknown meeting operation")
                # Raw transcript and personal summaries stay in the consent-gated view.
                from .crew import _clip

                if isinstance(result, dict):
                    public = {
                        key: value
                        for key, value in result.items()
                        if key in {"meeting_id", "task_id", "handoff_id", "status", "disclosure"}
                    }
                else:
                    public = {"status": "recorded"}
                text = "Meeting command recorded.\n" + "\n".join(
                    key.replace("_", " ").capitalize() + ": " + _clip(str(value), 300)
                    for key, value in public.items()
                )
            except (PermissionError, ValueError, KeyError, TypeError):
                text = (
                    "Meeting command denied or invalid; "
                    "use the original thread and current explicit consent."
                )
        self._reply(receipt_id, "meeting_command", text, freeze=True)

    def _reconcile_auxiliary(self):
        from .green_check import reconcile_investigations

        reconcile_investigations(self)
        """Report independently executed Notes/docs/trigger work in its original thread."""
        rows = self.store.connection.execute(
            "SELECT * FROM tasks WHERE status='IN_PROGRESS' AND "
            "(json_extract(source,'$.kind') IN ('meeting_notes','meeting_transcription',"
            "'confirmed_meeting_action','change_intelligence') "
            "OR json_extract(source,'$.trigger_type') IN "
            "('github_webhook','discord_security_scan') "
            "OR json_extract(source,'$.security_gate')=1) LIMIT 100"
        ).fetchall()
        for row in rows:
            task = self.store._row(row)
            if task["source"].get("room_id") not in {None, self.room_id}:
                continue
            if self.approvals.get(task["id"]) is not None:
                continue
            handoffs = [
                self.store._row(h)
                for h in self.store.connection.execute(
                    "SELECT * FROM handoffs WHERE task_id=?", (task["id"],)
                )
            ]
            if not handoffs or any(
                h["status"] != "completed" or h["sender"] != self.pm_id for h in handoffs
            ):
                continue
            statuses = [h["result"].get("status", "blocked") for h in handoffs]
            if task["source"].get("kind") in {"meeting_notes", "meeting_transcription"}:
                if self.meetings is None:
                    statuses = ["blocked_configuration"]
                else:
                    try:
                        self.meetings._consents(task["source"]["meeting_id"])
                    except PermissionError:
                        statuses = ["blocked_consent"]
            passed = all(
                status in {"completed", "evidence_found", "verified_success", "findings"}
                for status in statuses
            )
            self.store.transition_task(
                task["id"], "COMPLETED" if passed else "BLOCKED", task["version"]
            )
            self._reply(
                task["id"],
                "specialist_receipt",
                "Specialist results for task "
                + task["id"]
                + ": "
                + ", ".join(statuses)
                + ". Inspect the authenticated evidence/meeting view; "
                "proposals require human confirmation.",
            )
            if passed and task["source"].get("kind") == "meeting_notes":
                from .crew import _clip

                notes_result = handoffs[0]["result"]
                proposals = notes_result.get("notes", {}).get("action_items", [])
                preview = [
                    f"Meeting {task['source']['meeting_id']}: "
                    "proposed actions need human confirmation.",
                    f"Notes handoff: {handoffs[0]['id']}",
                ]
                for proposal in proposals[:15]:
                    preview.append(
                        f"- {_clip(proposal.get('proposal_id'), 100)}: "
                        + _clip(proposal.get("title"), 180)
                    )
                preview.append(
                    "Confirm or edit titles, owners, deadlines and criteria in the original thread "
                    "using a meeting confirm request."
                )
                self._reply(task["id"], "meeting_proposals", "\n".join(preview), freeze=True)

    async def _request(self, event):
        source = normalize_discord_event(
            event,
            self.bindings,
            allowed_guilds=self.allowed_guilds,
            allowed_users=self.allowed_users,
        )
        from .green_check import handle_investigation, investigation_followup

        if await handle_investigation(self, source):
            return
        from .security_intake import handle_scan_request

        if await handle_scan_request(self, source):
            return
        from .crew import handle_crew_command
        from .finding_selection import resolve_finding_request

        command = investigation_followup(self, source)
        if command is None:
            command = resolve_finding_request(
                self.store,
                source,
                pm_id=self.pm_id,
                engineering_job_key=self.engineering_job_key,
                engineering_repository=self.release_repository,
            )
        if command is None:
            command = handle_crew_command(
                self.store, source, can_cancel=source.user_id in self.approvals.approvers
            )
        if command is not None and "request" not in command:
            if command.get("task_id"):
                self._reply(
                    command["task_id"],
                    command["kind"] + "_" + source.message_id,
                    command["text"],
                    freeze=True,
                )
            elif command.get("durable_reply"):
                receipt_id = str(
                    uuid5(NAMESPACE_URL, f"crew-command:{source.room_id}:{source.message_id}")
                )
                self.store.create_task(
                    "Discord command receipt",
                    {
                        "room_id": source.room_id,
                        "thread_id": source.thread_id,
                        "message_id": source.message_id,
                        "user_id": source.user_id,
                        "channel_id": source.channel_id,
                        "guild_id": source.guild_id,
                        "command_receipt": True,
                    },
                    [],
                    task_id=receipt_id,
                )
                self._reply(receipt_id, "command_reply", command["text"], freeze=True)
            else:
                await self.switch.post_message(
                    command["text"], thread_id=source.thread_id or source.message_id
                )
            return
        try:
            request = (
                command["request"]
                if command is not None
                else (
                    human_request(source.body, self.engineering_job_key) or json.loads(source.body)
                )
            )
            if not isinstance(request, dict):
                request = {}
        except ValueError:
            if re.search(r'"type"\s*:\s*"approval"', source.body):
                # Recognize only the intent to give useful feedback. Never repair
                # malformed decisions or pass them through ordinary task intake.
                await self.switch.post_message(
                    "Invalid approval JSON. Resend the exact JSON with no trailing text. "
                    "No approval was recorded and no execution was authorized.",
                    thread_id=source.thread_id or source.message_id,
                )
                return
            request = {}
        if request.get("type") == "meeting":
            await self._meeting_request(source, request)
            return
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
        if request.get("type") in {"approval", "plan_review"}:
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
                if task["source"].get("investigation_task_id") and request["type"] == "approval":
                    self._reply(
                        task["id"],
                        "signed_approval_required_" + source.message_id,
                        "Use the signed /flip approve-engineering or approve-release control "
                        "with the exact task version. "
                        "Chat messages cannot authorize this workflow.",
                        freeze=True,
                    )
                    return
                if request["type"] == "plan_review":
                    self.orchestrator.review_step(
                        task["id"],
                        request.get("step_id"),
                        request["approved"],
                        source.user_id,
                        request["expected_version"],
                    )
                else:
                    self.approvals.decide(
                        task["id"],
                        request.get("stage"),
                        request["approved"],
                        source.user_id,
                        request["expected_version"],
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
                "channel_id": source.channel_id,
                "guild_id": source.guild_id,
                "project_id": self.project_id,
                **(
                    {"investigation_task_id": command["investigation_task_id"]}
                    if command and command.get("kind") == "investigation_fix"
                    else {}
                ),
                **(
                    {"selected_finding": command["trusted_selection"]}
                    if command is not None and "trusted_selection" in command
                    else {}
                ),
            },
            criteria,
            task_id=task_id,
        )
        if command is not None and "trusted_selection" in command:
            selected = command["trusted_selection"]
            self._reply(
                task_id,
                "finding_selected",
                f"Selected finding {selected['finding_id']} (rank {selected['rank']}) "
                f"from scan task {selected['scan_task_id']}.\n"
                f"Repository: {selected['repository']}; scan revision: {selected['revision']}.\n"
                "Scanner evidence still requires investigation; "
                "Engineering requires human approval.",
                freeze=True,
            )
        requested_kind = request.get("kind")
        if requested_kind is None and self.orchestrator.planner is not None:
            requested_kind = "orchestrate"
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
                }.get(requested_kind, "research"),
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
                    "Describe the work and add a 'Success criteria:' section with a short list "
                    "of what should pass. I need those details before I can delegate it."
                ),
                freeze=True,
            )
            return
        if requested_kind == "orchestrate":
            try:
                await self.orchestrator.start_with_planner(task_id)
                await self.orchestrator.reconcile_with_context(task_id)
            except (ValueError, HarnessError):
                self._reply(
                    task_id,
                    "plan_invalid",
                    "Orchestration needs valid criteria and an available configured planner.",
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
            if command and command.get("kind") == "investigation_fix":
                brain_metadata = command["company_brain"]
            elif self.company_brain is not None and existing_approval is None:
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
                    (existing_approval or {})
                    .get("spec", {})
                    .get("engineering", self.engineering_id),
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
            self._engineering_prompt(task_id)
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

    def _engineering_prompt(self, task_id):
        task = self.store.get_task(task_id)
        if task and task["status"] == "AWAITING_APPROVAL":
            version = task["version"]
            kind = "engineering_approval" if version == 1 else f"engineering_approval_v{version}"
            self._reply(
                task_id,
                kind,
                f"Engineering request {task_id} awaits human approval. "
                f"Task version: {version}. No Engineering action has started.\n\n"
                "To authorize the work, reply in this thread:\n"
                + (
                    f"/flip approve-engineering task_id:{task_id} version:{version}"
                    if task["source"].get("investigation_task_id")
                    else decision_phrase(task_id, "engineering", version)
                )
                + "\n\nRelease will require a separate approval after QA and review.",
                freeze=True,
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
            message = read_message(body, self.store)
            if not isinstance(message, dict):
                return
            if not isinstance(message.get("handoff_id"), str) or not message["handoff_id"]:
                return
            handoff = self.store.get_handoff(message["handoff_id"])
        except (ValueError, KeyError, TypeError):
            return
        if not handoff:
            return
        from .wire import result_matches

        if not result_matches(message, handoff):
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
        elif handoff["sender"] == self.identity and handoff[
            "recipient"
        ] == self.specialist_senders.get(event["payload"]["sender"]):
            if message.get("type") in {"handoff.accepted", "handoff.completed"}:
                self.store._event(
                    handoff["task_id"],
                    "switch.specialist_report",
                    {
                        "handoff_id": handoff["id"],
                        "recipient": handoff["recipient"],
                        "status": handoff["status"],
                        "result_sha256": message.get("result_sha256"),
                    },
                    self.store.get_task(handoff["task_id"])["version"],
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
                if task["status"] == "IN_PROGRESS" and not task["source"].get("parent_task_id"):
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
                    research_summary(handoff["result"]),
                    freeze=True,
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
            if task["source"].get("room_id") not in {None, self.room_id}:
                continue
            if payload.get("type", "").startswith("engineering_approval"):
                suffix = payload["type"].removeprefix("engineering_approval")
                version = 1 if suffix == "" else int(suffix.removeprefix("_v"))
                if task["status"] != "AWAITING_APPROVAL" or task["version"] != version:
                    self.store.connection.execute(
                        "UPDATE outbox SET status='suppressed' WHERE id=? AND status='pending'",
                        (item["id"],),
                    )
                    continue
            if payload.get("type") == "meeting_proposals":
                try:
                    if self.meetings is None:
                        raise PermissionError("Consent service unavailable")
                    self.meetings._consents(task["source"]["meeting_id"])
                except PermissionError:
                    self.store.connection.execute(
                        "UPDATE outbox SET status='suppressed' WHERE id=? AND status='pending'",
                        (item["id"],),
                    )
                    continue
            parent_id = task["source"].get("parent_task_id")
            parent = self.store.get_task(parent_id) if parent_id else None
            if payload.get("type") == "handoff" and (
                task["status"] in {"CANCELLED", "COMPLETED", "FAILED"}
                or (parent_id and (not parent or parent["status"] != "IN_PROGRESS"))
            ):
                continue
            thread = task["source"].get("thread_id") or task["source"].get("message_id")
            # Persisted logical delivery ID allows receiving workers to deduplicate
            # ambiguous transport outcomes; Switch itself has no POST idempotency API.
            body = (
                render_message(
                    item, task, handoff, role=self.role, target=self.names[item["recipient"]]
                )
                if handoff
                else ""
            )
            if item["recipient"] == "discord":
                result = await self.switch.post_message(payload["text"], thread_id=thread)
            else:
                result = await self.switch.send_targeted_message(
                    body, target_names=[self.names[item["recipient"]]], thread_id=thread
                )
            if item["recipient"] != "discord":
                statuses = result.get("target_statuses", {})
                if statuses.get(self.names[item["recipient"]]) in {
                    "offline",
                    "disconnected",
                    "no_session",
                    "awaiting_manual_poll",
                    "dormant",
                }:
                    return
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

    from .harnesses import load_harness_config
    from .memory import memory_from_settings
    from .settings import Settings

    settings = Settings()
    harness_roles = load_harness_config(settings.harness_config)
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
                project_id=settings.memory_project,
                checkpointer=saver,
                engineering_id=settings.engineering_agent_id,
                engineering_name=settings.engineering_name,
                engineering_job_key=settings.engineering_job_key,
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
                harness_roles=harness_roles,
                planner_policy={
                    "max_calls": settings.harness_planner_max_calls,
                    "max_steps": settings.harness_planner_max_steps,
                    "max_prompt_chars": settings.harness_planner_max_prompt_chars,
                    "fallback_on_failure": settings.harness_planner_fallback,
                },
            )
            from .organization import load_organization_config, meeting_service

            organization = load_organization_config(settings.organization_config)
            from .organization import configure_security

            worker.security_gate = configure_security(store, settings, organization)
            worker.green_check_routes = organization.get("green_check", {})
            worker.security_scans = organization.get("security_scans", {})
            if not isinstance(worker.security_scans, dict):
                raise ValueError("Security scan routes must be an operator-owned mapping")

            worker.meetings = meeting_service(
                store, settings, organization, enable_voice_provider=False
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
