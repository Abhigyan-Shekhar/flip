"""Adapters for independent Switch workers; all executable configuration is operator-owned."""

from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from .approval import ApprovalWorkflow
from .store import encode


def schedule_documentation(store, task_id, pm_id, documentation_id):
    data = ApprovalWorkflow(store).get(task_id)
    task = store.get_task(task_id)
    if (
        not data
        or not task
        or data.get("phase") not in {"human_review", "release", "pr_open", "completed"}
    ):
        return None
    revision = data.get("revision")
    if not revision or not documentation_id or task["status"] in {"CANCELLED", "BLOCKED", "FAILED"}:
        return None
    try:
        ApprovalWorkflow(store).evidence(task_id)
    except (PermissionError, ValueError, KeyError):
        # Defer until configured Security and independent checks have completed.
        return None
    identity = str(uuid5(NAMESPACE_URL, f"change-report:{task_id}:{revision}"))
    with store._lock, store.transaction():
        existing = store.get_task(identity)
        if existing:
            return existing
        child = store.create_task(
            f"Document verified change for task {task_id} at {revision}",
            {
                **task["source"],
                "kind": "change_intelligence",
                "evidence_task_id": task_id,
                "revision": revision,
            },
            ["Use actual Git diff and current QA/Reviewer receipts; draft only."],
            identity,
        )
        child = store.transition_task(identity, "PLANNED", child["version"])
        child = store.transition_task(identity, "IN_PROGRESS", child["version"])
        store.create_handoff(
            identity,
            pm_id,
            documentation_id,
            child["objective"],
            child["acceptance_criteria"],
            child["version"],
            handoff_id=str(uuid5(NAMESPACE_URL, identity + ":docs")),
        )
        return child


class ChangeIntelligenceExecutor:
    def __init__(self, store, agent_id, allowed_roots, company_brain=None):
        self.store, self.agent_id = store, agent_id
        self.roots = [Path(root).resolve() for root in allowed_roots]
        self.company_brain = company_brain

    async def __call__(self, state):
        from .change_intelligence import build_change_report

        task = self.store.get_task(state["task_id"])
        handoff = self.store.get_handoff(state["handoff_id"])
        if (
            not task
            or not handoff
            or handoff["recipient"] != self.agent_id
            or handoff["task_id"] != task["id"]
        ):
            return {"status": "blocked", "reason": "authorized_documentation_assignment_required"}
        origin = self.store.get_task(task["source"].get("evidence_task_id", ""))
        data = ApprovalWorkflow(self.store).get(origin["id"]) if origin else None
        if (
            not data
            or not origin
            or origin["status"] in {"CANCELLED", "BLOCKED", "FAILED"}
            or data.get("revision") != task["source"].get("revision")
        ):
            return {"status": "blocked", "reason": "current_engineering_receipt_required"}
        try:
            approvals = ApprovalWorkflow(self.store)
            qa = approvals.evidence(origin["id"])
            review = approvals.review_evidence(origin["id"])
            checkout = Path(qa["checkout"]).resolve()
            if not any(checkout.is_relative_to(root) for root in self.roots):
                raise PermissionError("Documentation checkout is outside allowed roots")
            report = await build_change_report(
                str(checkout),
                review["base_revision"],
                data["revision"],
                task_id=origin["id"],
                requester=origin["source"].get("user_id"),
                assigned_agent=data["spec"]["engineering"],
                tests=[
                    encode(
                        {
                            key: item.get(key)
                            for key in ("command", "exit_code", "revision", "timed_out")
                        }
                    )
                    for item in qa.get("commands", [])
                ],
                reviewer_findings=[str(item) for item in review.get("findings", [])],
                final_outcome=data["phase"],
                company_brain=self.company_brain,
                workflow_evidence={
                    "qa_handoff": data["qa_handoff"],
                    "review_handoff": data["review_handoff"],
                },
            )
            return {
                "status": "completed",
                "role": "documentation",
                "change_report": report.to_dict(),
                "summary": report.pr_summary_draft[:1800],
                "revision": data["revision"],
            }
        except (OSError, ValueError, KeyError, PermissionError, RuntimeError):
            return {
                "status": "blocked",
                "reason": "revision_pinned_documentation_evidence_unavailable",
            }


def configured_executor(role, capability, store, connection, settings):
    mode = capability.get("mode")
    if mode == "harness":
        from .harnesses import HarnessExecutor, load_harness_config

        roles = load_harness_config(settings.harness_config)
        if role not in roles:
            raise ValueError("No operator-configured harness for this specialist")
        return HarnessExecutor(roles[role])
    if role == "voice" and mode == "meeting_transcription":
        from .meetings import MeetingVoiceExecutor
        from .organization import load_organization_config, meeting_service

        config = load_organization_config(settings.organization_config)
        service = meeting_service(store, settings, config, enable_voice_provider=True)
        if service is None:
            raise ValueError("Meeting service configuration required")
        return MeetingVoiceExecutor(service, agent_id=connection.agent_id)
    if role == "notes" and mode == "meeting_notes":
        from .notes import NotesExecutor
        from .organization import load_organization_config, meeting_service

        config = load_organization_config(settings.organization_config)
        service = meeting_service(store, settings, config, enable_voice_provider=False)
        if service is None:
            raise ValueError("Meeting service configuration required")
        from .company_brain import company_brain_from_settings
        from .meeting_context import gather_meeting_context

        brain = company_brain_from_settings(settings) if settings.company_brain_enabled else None
        context_actor = config.get("meetings", {}).get("context_actor_id")
        lesson_service = None
        if context_actor and config.get("knowledge"):
            from .lessons import LessonService
            from .memory import memory_from_settings

            lesson_service = LessonService(
                store,
                trusted_actors=config["knowledge"].get("trusted_actors", {}),
                memory=memory_from_settings(settings),
            )

        async def context_provider(state, meeting):
            return await gather_meeting_context(
                store,
                meeting,
                company_brain=brain,
                lessons=lesson_service,
                actor_id=context_actor,
                project_id=settings.memory_project,
            )

        return NotesExecutor(
            service, agent_id=connection.agent_id, context_provider=context_provider
        )
    if role == "documentation" and mode == "change_intelligence":
        from .company_brain import company_brain_from_settings

        return ChangeIntelligenceExecutor(
            store,
            connection.agent_id,
            capability.get("allowed_repo_roots", []),
            company_brain_from_settings(settings) if settings.company_brain_enabled else None,
        )
    if role == "qa" and mode == "triggered_qa":
        from .isolation import DockerIsolation
        from .triggers import TriggeredQAExecutor

        isolation = (
            DockerIsolation.from_config(capability["container"])
            if capability.get("container") is not None
            else None
        )
        if capability.get("require_isolation") and isolation is None:
            raise ValueError("Isolated triggered QA runner required")
        return TriggeredQAExecutor(
            store,
            capability["repo_root"],
            connection.agent_id,
            capability["repository"],
            capability["commands"],
            timeout=capability.get("timeout", 120),
            isolation=isolation,
        )
    if role == "security" and mode == "triggered_security":
        from .isolation import DockerIsolation
        from .security import ScannerConfig, SecurityRunner
        from .triggers import TriggeredSecurityExecutor

        scanners = [ScannerConfig(**item) for item in capability.get("scanners", [])]
        isolation = (
            DockerIsolation.from_config(capability["container"])
            if capability.get("container") is not None
            else None
        )
        if capability.get("require_isolation") and isolation is None:
            raise ValueError("Isolated security scanner runner required")
        return TriggeredSecurityExecutor(
            store,
            capability["repo_root"],
            connection.agent_id,
            capability["repository"],
            SecurityRunner(
                scanners, isolation=isolation, artifact_dir=capability.get("artifact_dir")
            ),
        )
    if role == "security" and mode == "engineering_security_gate":
        from .isolation import DockerIsolation
        from .security import ScannerConfig, SecurityRunner
        from .security_gate import GateSecurityExecutor

        isolation = (
            DockerIsolation.from_config(capability["container"])
            if capability.get("container") is not None
            else None
        )
        if capability.get("require_isolation") and isolation is None:
            raise ValueError("Isolated security scanner runner required")
        return GateSecurityExecutor(
            store,
            connection.agent_id,
            SecurityRunner(
                [ScannerConfig(**item) for item in capability.get("scanners", [])],
                isolation=isolation,
                artifact_dir=capability.get("artifact_dir"),
            ),
        )
    return None
