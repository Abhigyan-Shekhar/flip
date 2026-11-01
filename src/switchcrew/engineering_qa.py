"""Standalone specialist processes using AgentConnection/AgentRuntime contracts.

Each process needs an explicit private config and separate integration database.
No PM/Research settings, containers or credential files are loaded.
"""

import argparse
import asyncio
import json
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from .engineering import (
    ConsoleCodex,
    EngineeringExecutor,
    Ledger,
    assignment_id,
    enqueue_independent_qa,
    qa_handoff_id,
)
from .graphs import build_agent_graph
from .memory import NullMemory, memory_from_settings
from .qa import QAExecutor
from .runtime import AgentRuntime, load_connection
from .settings import Settings
from .store import Store
from .switch import SwitchClient


class IntegrationRuntime(AgentRuntime):
    def __init__(self, *args, after_pending=None):
        super().__init__(*args)
        self.after_pending = after_pending
        self.processing = asyncio.Lock()
        self.delivery = asyncio.Lock()

    async def process_pending(self):
        async with self.processing:
            await super().process_pending()
            if self.after_pending:
                self.after_pending()

    async def flush_outbox(self):
        async with self.delivery:
            await super().flush_outbox()

    async def poll(self):
        while True:
            await asyncio.sleep(2)
            # Poll receipts even when the SSE stream has no new messages.
            if self.switch.generation is not None:
                await self.process_pending()
                await self.flush_outbox()


async def run(config_file):
    path = Path(config_file)
    if path.stat().st_mode & 0o077:
        raise ValueError("Integration configuration must be private")
    config = json.loads(path.read_text())
    role = config["role"]
    if role not in {"engineering", "qa"}:
        raise ValueError("This entry point supports Engineering and QA only")
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
    client = SwitchClient(
        config["switch_url"],
        connection.agent_id,
        connection.api_key,
        connection_id=str(uuid5(NAMESPACE_URL, f"switchcrew:{role}:{connection.agent_id}")),
        rooms=[connection.room_id],
    )

    async def execute(state):
        job = config.get("jobs", {}).get(state["handoff_id"])
        if role == "qa" and job is None:
            for upstream, review in config.get("reviews", {}).items():
                handoff = store.get_handoff(upstream)
                result = (handoff or {}).get("result") or {}
                revision = result.get("final_revision")
                if revision and state["handoff_id"] == qa_handoff_id(upstream, revision):
                    job = {
                        **review,
                        "implementation_assignment": assignment_id(upstream),
                        "revision": revision,
                    }
                    break
        if job is None:
            return {"status": "blocked_configuration", "reason": "operator_job_not_configured"}
        if role == "engineering":
            session = ConsoleCodex(
                config["control_file"],
                job["console_agent_id"],
                job["codex_name"],
                client,
                console_database=config["console_database"],
                switch_agent_id=job["codex_switch_agent_id"],
            )
            executor = EngineeringExecutor(
                ledger, config["checkout_root"], job["source"], job["base_revision"], session
            )
        else:
            executor = QAExecutor(
                ledger,
                config["checkout_root"],
                job["implementation_assignment"],
                job["revision"],
                job["commands"],
                job.get("timeout", 120),
            )
        return await executor(state)

    # Pending executor results must be reconciled afresh, never checkpoint-cached.
    graph = build_agent_graph(role, executor=execute, memory=memory)

    def after_pending():
        if role == "engineering":
            for handoff_id in config.get("jobs", {}):
                handoff = store.get_handoff(handoff_id)
                if handoff:
                    enqueue_independent_qa(
                        store, handoff_id, connection.agent_id, config["qa_agent_id"]
                    )

    runtime = IntegrationRuntime(connection, store, client, graph, after_pending=after_pending)
    try:
        async with asyncio.TaskGroup() as group:
            group.create_task(runtime.run())
            group.create_task(runtime.poll())
    finally:
        await client.close()
        store.close()
        ledger.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    asyncio.run(run(parser.parse_args().config))
