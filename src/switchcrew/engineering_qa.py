"""Independent approved Engineering, QA and Release processes over Switch.

Each process reads its own private registry/capability configuration and shares
application approval state; Engineering, QA and Release share a separate ledger.
No root .env or PM/Research credential files are loaded.
"""

import argparse
import asyncio
import json
import logging
import stat
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from .approval import ApprovalWorkflow
from .employees import EmployeeDirectory
from .engineering import (
    ConsoleCodex,
    EngineeringExecutor,
    Ledger,
    assignment_id,
)
from .graphs import build_agent_graph
from .memory import NullMemory, memory_from_settings
from .qa import QAExecutor
from .release import GitHubPublisher, ReleaseExecutor
from .reviewer import ReviewerExecutor
from .runtime import AgentRuntime, load_connection, ownership_lost
from .settings import Settings
from .store import Store
from .switch import SwitchClient

log = logging.getLogger("switchcrew.runtime")


class _EmployeeConsoleSession:
    """Recheck task/assignment authorization around each Console network call."""

    def __init__(self, session, authorize, record_session):
        self.session = session
        self.authorize = authorize
        self.record_session = record_session
        self.session_id = None

    async def validate(self, checkout):
        self.authorize()
        result = await self.session.validate(checkout)
        self.authorize()
        return result

    async def find(self, identity):
        self.authorize()
        session_id = await self.session.find(identity)
        if session_id:
            self.session_id = session_id
            self.record_session(session_id, "recovering")
        self.authorize()
        return session_id

    async def create(self, identity):
        self.authorize()
        session_id = await self.session.create(identity)
        if not isinstance(session_id, str) or not session_id:
            raise ValueError("Console did not return a task session identity")
        self.session_id = session_id
        self.record_session(session_id, "recovering")
        self.authorize()
        return session_id

    async def deliver(self, identity, checkout, question, criteria):
        self.authorize()
        event_id = await self.session.deliver(identity, checkout, question, criteria)
        if not isinstance(event_id, str) or not event_id:
            raise ValueError("Console delivery did not return its Switch event identity")
        if self.session_id:
            self.record_session(self.session_id, "connected")
        self.authorize()
        return event_id


def _employee_scoped_path(path, workspace_root, *, must_exist):
    candidate = Path(path).expanduser().resolve(strict=must_exist)
    root = Path(workspace_root).resolve(strict=True)
    if not candidate.is_relative_to(root):
        raise ValueError("Engineering source or checkout root is outside employee workspace")
    return candidate


def _employee_console_fields_match(job, profile, switch_id):
    registered_switch = profile["switch_agent_id"]
    provider_switch = profile["codex_switch_agent_id"]
    return (
        isinstance(job.get("codex_switch_agent_id"), str)
        and job["codex_switch_agent_id"] == provider_switch
        and registered_switch == switch_id
        and provider_switch != switch_id
        and job.get("console_agent_id", profile["console_agent_id"]) == profile["console_agent_id"]
        and job.get("codex_name", profile["codex_name"]) == profile["codex_name"]
    )


class IntegrationRuntime(AgentRuntime):
    def __init__(self, *args, after_pending=None):
        super().__init__(*args)
        self.after_pending = after_pending
        self.processing = asyncio.Lock()
        self.delivery = asyncio.Lock()
        self.ready = asyncio.Event()

    async def on_connected(self):
        self.ready.clear()
        await super().on_connected()
        if getattr(self.switch, "connected", True):
            self.ready.set()

    def on_disconnected(self):
        self.ready.clear()

    def is_ready(self):
        return self.ready.is_set() and super().is_ready()

    async def run(self):
        try:
            await super().run()
        finally:
            self.ready.clear()

    async def process_pending(self):
        async with self.processing:
            if not self.ready.is_set():
                return
            await super().process_pending()
            if self.after_pending:
                self.after_pending()

    async def flush_outbox(self):
        async with self.delivery:
            if not self.ready.is_set():
                return
            await super().flush_outbox()

    async def poll(self):
        while True:
            await self.ready.wait()
            await asyncio.sleep(2)
            # Poll receipts even when the SSE stream has no new messages.
            if self.ready.is_set():
                await self.process_pending()
                await self.flush_outbox()


def configured_executor(config, connection, client, store, ledger, approvals):
    """Wire operator-owned capabilities; no privileged runtime has an approval bypass."""
    role = connection.role
    employee_config = config.get("employee")
    employee_directory = None
    if employee_config is not None:
        if role != "engineering" or not isinstance(employee_config, dict):
            raise ValueError("Employee registrations can only configure an Engineering runtime")
        allowed_assigners = employee_config.get("allowed_assigners")
        employee_id, actor_id = (
            employee_config.get("employee_id"),
            employee_config.get("actor_id"),
        )
        if (
            not isinstance(allowed_assigners, list)
            or not allowed_assigners
            or not isinstance(employee_id, str)
            or not employee_id
            or not isinstance(actor_id, str)
            or not actor_id
        ):
            raise ValueError("Employee runtime identity and assignment authorities are required")
        employee_directory = EmployeeDirectory.from_file(
            store,
            employee_config["registrations_file"],
            allowed_assigners=set(allowed_assigners),
        )
    lesson_service = None
    learning = config.get("learning")
    if learning:
        from .lessons import LessonService

        lesson_service = LessonService(
            store,
            trusted_actors=learning["trusted_actors"],
            memory=memory_from_settings(Settings(_env_file=None, **config.get("memory", {}))),
        )

    async def execute(state):
        managed = approvals.get(state["task_id"])
        if not managed:
            return {"status": "blocked", "reason": "human_engineering_approval_required"}
        if role == "release":
            release_config = config["release"]
            publisher = GitHubPublisher(
                release_config["repository"], release_config["base"], release_config["token"]
            )
            return await ReleaseExecutor(approvals, ledger, connection.agent_id, publisher)(state)
        if role == "engineering":
            try:
                workflow = approvals.require_engineering(state, connection.agent_id)
            except PermissionError:
                return {"status": "blocked", "reason": "human_engineering_approval_required"}
        elif role == "reviewer":
            workflow = managed
            task = store.get_task(state["task_id"])
            if (
                managed.get("phase") != "review"
                or managed.get("spec", {}).get("reviewer") != connection.agent_id
                or managed.get("review_handoff") != state.get("handoff_id")
                or not task
                or task["status"] != "QA_REVIEW"
                or not approvals.parent_active(task)
            ):
                return {"status": "blocked", "reason": "review_assignment_mismatch"}
        elif role == "qa":
            if (
                managed["phase"] != "qa"
                or not managed.get("engineering_approval", {}).get("approved")
                or store.get_task(state["task_id"])["status"] != "QA_REVIEW"
                or not approvals.parent_active(store.get_task(state["task_id"]))
                or managed.get("qa_handoff") != state["handoff_id"]
                or managed["spec"]["qa"] != connection.agent_id
            ):
                return {"status": "blocked", "reason": "qa_assignment_mismatch"}
        else:
            return {"status": "blocked_configuration", "reason": "unsupported_privileged_role"}
        job = config.get("job_templates", {}).get(managed["spec"]["job_key"])
        if job is None:
            return {"status": "blocked_configuration", "reason": "operator_job_not_configured"}
        task = store.get_task(state["task_id"])
        investigation_id = task["source"].get("investigation_task_id") if task else None
        if role == "engineering" and investigation_id:
            origin = store.get_task(investigation_id)
            source = origin["source"] if origin else {}
            if (
                not origin
                or origin["status"] != "COMPLETED"
                or job.get("base_revision") != source.get("revision")
                or Path(job.get("source", "")).resolve()
                != Path(source.get("repo_root", "")).resolve()
                or managed["spec"].get("repository") != source.get("repository")
            ):
                return {
                    "status": "blocked_configuration",
                    "reason": "engineering_job_must_match_investigated_repository_and_revision",
                }
        from .isolation import DockerIsolation

        isolation = (
            DockerIsolation.from_config(job["container"])
            if job.get("container") is not None
            else None
        )
        if config.get("require_isolation") and isolation is None:
            return {"status": "blocked_configuration", "reason": "isolated_runner_required"}
        if role == "engineering":
            correction = (
                workflow.get("correction") if workflow.get("phase") == "engineering" else None
            )
            if correction:
                # Existing approval data owns the source and revision. Ensure the
                # previous result still matches the separate execution ledger.
                previous = correction.get("implementation") or {}
                previous_identity = previous.get("assignment_id")
                persisted = ledger.get(previous_identity) if previous_identity else None
                persisted_result = (persisted or {}).get("result") or {}
                if any(
                    persisted_result.get(key) != previous.get(key)
                    for key in ("assignment_id", "checkout", "base_revision", "final_revision")
                ):
                    return {
                        "status": "blocked_configuration",
                        "reason": "correction_implementation_unavailable",
                    }
            employee_profile = None
            employee_authorize = None
            employee_record_session = None
            checkout_root = config["checkout_root"]
            job_for_attempt = job
            source = (correction or {}).get("implementation", {}).get("checkout", job.get("source"))
            if employee_directory is not None:
                try:
                    employee_profile = employee_directory.authorize_assignment(
                        employee_config["employee_id"],
                        state["task_id"],
                        actor_id=employee_config["actor_id"],
                        handoff_id=state["handoff_id"],
                    )
                    if employee_profile["switch_agent_id"] != connection.agent_id:
                        raise PermissionError("Employee Switch identity does not match runtime")
                    root = Path(employee_profile["workspace_root"]).resolve(strict=True)
                    job_source = _employee_scoped_path(job.get("source"), root, must_exist=True)
                    source = _employee_scoped_path(source or job_source, root, must_exist=True)
                    checkout_root = _employee_scoped_path(
                        config["checkout_root"], root, must_exist=False
                    )
                    if not _employee_console_fields_match(
                        job, employee_profile, connection.agent_id
                    ):
                        raise ValueError(
                            "Employee Console/ Switch identities do not match registration"
                        )
                    job_for_attempt = {
                        **job,
                        "console_agent_id": employee_profile["console_agent_id"],
                        "codex_name": employee_profile["codex_name"],
                        "codex_switch_agent_id": employee_profile["codex_switch_agent_id"],
                    }

                    def employee_authorize(value=None):
                        value = value or state
                        approvals.require_engineering(value, connection.agent_id)
                        current = store.get_task(value["task_id"])
                        if not current or current["status"] not in {
                            "PLANNED",
                            "IN_PROGRESS",
                            "QA_REVIEW",
                            "BLOCKED",
                        }:
                            raise PermissionError("Employee task is cancelled or inactive")
                        profile = employee_directory.authorize_assignment(
                            employee_config["employee_id"],
                            value["task_id"],
                            actor_id=employee_config["actor_id"],
                            handoff_id=value["handoff_id"],
                        )
                        if profile["switch_agent_id"] != connection.agent_id:
                            raise PermissionError("Employee Switch identity changed")
                        _employee_scoped_path(job_source, root, must_exist=True)
                        _employee_scoped_path(checkout_root, root, must_exist=False)
                        _employee_scoped_path(source, root, must_exist=True)

                    employee_authorize(state)

                    def employee_record_session(session_id, status):
                        return employee_directory.record_session(
                            state["task_id"],
                            employee_config["employee_id"],
                            state["handoff_id"],
                            session_id,
                            status,
                        )
                except PermissionError:
                    return {"status": "blocked", "reason": "employee_assignment_required"}
                except (OSError, TypeError, ValueError, KeyError):
                    return {
                        "status": "blocked_configuration",
                        "reason": "employee_registration_or_workspace_invalid",
                    }
                session = ConsoleCodex(
                    employee_profile["control_file"],
                    employee_profile["console_agent_id"],
                    employee_profile["codex_name"],
                    client,
                    console_database=employee_profile["console_database"],
                    switch_agent_id=employee_profile["codex_switch_agent_id"],
                )
                session = _EmployeeConsoleSession(
                    session, employee_authorize, employee_record_session
                )
            else:
                job_for_attempt = {
                    **job,
                    **config.get("console_agents", {}).get(assignment_id(state["handoff_id"]), {}),
                }
                waiting_for_managed_console = False
                if job_for_attempt.get("managed_console_directory"):
                    descriptor = Path(job_for_attempt["managed_console_directory"]) / (
                        assignment_id(state["handoff_id"]) + ".json"
                    )
                    try:
                        info = descriptor.lstat()
                    except FileNotFoundError:
                        waiting_for_managed_console = True
                    else:
                        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
                            return {
                                "status": "blocked_configuration",
                                "reason": "managed_descriptor_not_private",
                            }
                        job_for_attempt["managed_console"] = json.loads(descriptor.read_text())
                if waiting_for_managed_console:
                    session = None
                elif "managed_console" in job_for_attempt:
                    from .managed_console import ManagedConsoleCodex

                    session = ManagedConsoleCodex(
                        job_for_attempt["managed_console"],
                        client,
                        assignment_id(state["handoff_id"]),
                    )
                else:
                    session = ConsoleCodex(
                        config["control_file"],
                        job_for_attempt["console_agent_id"],
                        job_for_attempt["codex_name"],
                        client,
                        console_database=config["console_database"],
                        switch_agent_id=job_for_attempt["codex_switch_agent_id"],
                    )
            executor = EngineeringExecutor(
                ledger,
                checkout_root,
                source,
                (correction or {})
                .get("implementation", {})
                .get("final_revision", job_for_attempt["base_revision"]),
                session,
                authorize=employee_authorize
                or (lambda value: approvals.require_engineering(value, connection.agent_id)),
                review_base_revision=(correction or {})
                .get("implementation", {})
                .get("base_revision"),
                expected_source_revision=(correction or {})
                .get("implementation", {})
                .get("final_revision"),
            )
            if employee_directory is None and waiting_for_managed_console:
                preparation = await executor(state)
                if preparation.get("reason") == "managed_codex_session_not_configured":
                    return {
                        **preparation,
                        "status": "pending_execution",
                        "reason": "awaiting_managed_console_provisioning",
                    }
                return preparation
        elif role == "qa":

            def qa_authorized(value):
                current = approvals.get(value["task_id"])
                task = store.get_task(value["task_id"])
                if (
                    not current
                    or current.get("phase") != "qa"
                    or current.get("qa_handoff") != value["handoff_id"]
                    or current.get("spec", {}).get("qa") != connection.agent_id
                    or not task
                    or task["status"] != "QA_REVIEW"
                    or not approvals.parent_active(task)
                ):
                    raise PermissionError("QA task/parent is no longer active")

            executor = QAExecutor(
                ledger,
                config["checkout_root"],
                assignment_id(managed["engineering_handoff"]),
                managed["revision"],
                job.get("commands", []),
                job.get("timeout", 120),
                authorize=qa_authorized,
                isolation=isolation,
            )
        else:

            def review_authorized(value):
                current = approvals.get(value["task_id"])
                task = store.get_task(value["task_id"])
                if (
                    not current
                    or current.get("phase") != "review"
                    or current.get("review_handoff") != value["handoff_id"]
                    or current.get("spec", {}).get("reviewer") != connection.agent_id
                    or not task
                    or task["status"] != "QA_REVIEW"
                    or not approvals.parent_active(task)
                ):
                    raise PermissionError("Review assignment is no longer active")
                return True

            implementation = {
                "handoff_id": managed["engineering_handoff"],
                "final_revision": managed["revision"],
            }
            job = config.get("job_templates", {}).get(managed["spec"]["job_key"], {})
            executor = ReviewerExecutor(
                ledger,
                config.get("review_root", config["checkout_root"]),
                assignment_id(implementation["handoff_id"]),
                implementation["final_revision"],
                job.get("review_commands", job.get("commands", [])),
                job.get("review_timeout", job.get("timeout", 120)),
                authorize=review_authorized,
                isolation=isolation,
            )
        if role == "engineering" and lesson_service is not None:
            from .learning_runtime import lesson_prompt

            state = await lesson_prompt(store, lesson_service, state, actor_id=learning["actor_id"])
        result = await executor(state)
        if role == "engineering" and employee_directory is not None:
            execution = ledger.get(assignment_id(state["handoff_id"])) or {}
            session_id = result.get("session_id") or execution.get("session_id")
            try:
                employee_authorize(state)
            except PermissionError:
                if isinstance(session_id, str) and session_id and employee_record_session:
                    employee_record_session(session_id, "blocked")
                return {
                    "status": "pending_execution",
                    "assignment_id": assignment_id(state["handoff_id"]),
                    "reason": "employee_assignment_changed_during_execution",
                }
            if isinstance(session_id, str) and session_id and employee_record_session:
                status = (
                    "completed"
                    if result.get("status") in {"implemented", "implementation_failed"}
                    else "blocked"
                    if result.get("status") in {"blocked", "blocked_configuration"}
                    else "recovering"
                )
                try:
                    saved_session = employee_record_session(session_id, status)
                except (PermissionError, ValueError):
                    return {
                        "status": "pending_execution",
                        "assignment_id": assignment_id(state["handoff_id"]),
                        "reason": "employee_session_reconciliation_required",
                    }
                if saved_session["status"] == "blocked":
                    return {
                        "status": "pending_execution",
                        "assignment_id": assignment_id(state["handoff_id"]),
                        "reason": "employee_assignment_changed_during_execution",
                    }
        return result

    return execute


async def run(config_file):
    path = Path(config_file)
    if path.stat().st_mode & 0o077:
        raise ValueError("Integration configuration must be private")
    config = json.loads(path.read_text())
    # Use the same origin/HTTPS validation as the established worker path.
    switch_url = Settings(_env_file=None, switch_base_url=config["switch_url"]).switch_base_url
    role = config["role"]
    if role not in {"engineering", "qa", "reviewer", "release"}:
        raise ValueError("This entry point supports Engineering, QA, Reviewer and Release only")
    if Path(config["registry"]).stat().st_mode & 0o077:
        raise ValueError("Runtime registry must be private")
    connection = load_connection(config["registry"], role)
    if connection.room_id != config["test_room_id"]:
        raise ValueError("Registry does not match the explicitly isolated test room")
    database = Path(config["database"]).resolve()
    ledger_path = Path(config["ledger"]).resolve()
    if database == ledger_path:
        raise ValueError("Application state and execution ledger require separate files")
    # No root .env is read. Optional Waggle uses the project's existing adapter.
    memory_config = config.get("memory", {})
    memory = (
        memory_from_settings(Settings(_env_file=None, **memory_config))
        if memory_config.get("memory_enabled") is True
        else NullMemory()
    )
    store, ledger = Store(database), Ledger(ledger_path)
    approvals = ApprovalWorkflow(store)
    client = SwitchClient(
        switch_url,
        connection.agent_id,
        connection.api_key,
        connection_id=str(uuid5(NAMESPACE_URL, f"switchcrew:{role}:{connection.agent_id}")),
        rooms=[connection.room_id],
    )

    execute = configured_executor(config, connection, client, store, ledger, approvals)

    # Pending executor results must be reconciled afresh, never checkpoint-cached.
    graph = build_agent_graph(role, executor=execute, memory=memory)

    def after_pending():
        for row in store.connection.execute("SELECT task_id FROM approval_workflows").fetchall():
            approvals.reconcile(row[0])
        if config.get("learning"):
            from .learning_runtime import reconcile_lesson_usage
            from .lessons import LessonService

            learning = config["learning"]
            lessons = LessonService(store, trusted_actors=learning["trusted_actors"], memory=memory)
            reconcile_lesson_usage(store, lessons, actor_id=learning["actor_id"])

    runtime = IntegrationRuntime(connection, store, client, graph, after_pending=after_pending)
    try:
        async with asyncio.TaskGroup() as group:
            group.create_task(runtime.run())
            group.create_task(runtime.poll())
    finally:
        await client.close()
        store.close()
        ledger.close()


async def run_integration(config_file):
    try:
        await run(config_file)
    except Exception as exc:
        terminal = ownership_lost(exc)
        log.error(
            "integration_ownership_lost" if terminal else "integration_failed",
            extra={"error_type": type(exc).__name__},
        )
        return 0 if terminal else 1
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    from .logging import configure_logging

    configure_logging()
    raise SystemExit(asyncio.run(run_integration(parser.parse_args().config)))
