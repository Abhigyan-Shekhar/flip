"""Independent approved Engineering, QA and Release processes over Switch.

Each process reads its own private registry/capability configuration and shares
application approval state; Engineering, QA and Release share a separate ledger.
No root .env or PM/Research credential files are loaded.
"""

import argparse
import asyncio
import json
import logging
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from .approval import ApprovalWorkflow
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
            job_for_attempt = {
                **job,
                **config.get("console_agents", {}).get(assignment_id(state["handoff_id"]), {}),
            }
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
                config["checkout_root"],
                (correction or {})
                .get("implementation", {})
                .get("checkout", job_for_attempt["source"]),
                (correction or {})
                .get("implementation", {})
                .get("final_revision", job_for_attempt["base_revision"]),
                session,
                authorize=lambda value: approvals.require_engineering(value, connection.agent_id),
                review_base_revision=(correction or {})
                .get("implementation", {})
                .get("base_revision"),
                expected_source_revision=(correction or {})
                .get("implementation", {})
                .get("final_revision"),
            )
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
            )
        return await executor(state)

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
