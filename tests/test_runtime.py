import asyncio
import json

import pytest

from switchcrew.runtime import AgentConnection, AgentRuntime
from switchcrew.store import Store
from switchcrew.wire import read_message, render_message


class Switch:
    agent_id = "research"
    cursor = None

    def __init__(self, events=()):
        self.stream = events
        self.sent = []
        self.acks = []

    async def send_targeted_message(self, body, target_names, thread_id):
        self.sent.append(read_message(body, self.store))
        return {"target_statuses": {target_names[0]: "live"}, "event_id": "external"}

    async def connect_to_room(self, room):
        assert room == "room"

    async def acknowledge(self, sequence):
        self.acks.append(sequence)
        self.cursor = sequence

    async def events(self):
        for event in self.stream:
            yield event


def setup_runtime(tmp_path, switch=None):
    store = Store(tmp_path / "state.db")
    task = store.create_task("Question", {}, ["Evidence"])
    handoff = store.create_handoff(task["id"], "pm", "research", "Question", ["Evidence"], 0)
    switch = switch or Switch()
    switch.store = store
    calls = []

    class Graph:
        checkpointer = None

        async def ainvoke(self, state, config):
            assert switch.sent[0]["type"] == "handoff.accepted"
            calls.append(state)
            return {"result": {"status": "blocked", "reason": "No executor configured"}}

    connection = AgentConnection(
        "research",
        "research",
        "participant-research",
        "Research",
        "secret",
        "room",
        {"participant-pm": "pm"},
        {"pm": "PM"},
    )
    runtime = AgentRuntime(connection, store, switch, Graph())
    event = {
        "type": "message",
        "room_id": "room",
        "sequence": 4,
        "payload": {
            "sender": "participant-pm",
            "message_id": "transport-id",
            "body": json.dumps(
                {
                    "type": "handoff",
                    "task_id": task["id"],
                    "handoff_id": handoff["id"],
                    "delivery_id": "durable-id",
                }
            ),
        },
    }
    return runtime, event, handoff, calls


@pytest.mark.parametrize("result_status", ["verified_success", "pending_execution"])
async def test_cancel_during_execution_blocks_late_result(tmp_path, result_status):
    runtime, event, handoff, _ = setup_runtime(tmp_path)
    started, release = asyncio.Event(), asyncio.Event()

    class SuspendedGraph:
        name = "switchcrew_research"
        checkpointer = None

        async def ainvoke(self, state, config):
            started.set()
            await release.wait()
            return {"result": {"status": result_status}}

    runtime.graph = SuspendedGraph()
    await runtime.receive(event)
    processing = asyncio.create_task(runtime.process_pending())
    await started.wait()
    task = runtime.store.get_task(handoff["task_id"])
    runtime.store.transition_task(task["id"], "CANCELLED", task["version"])
    release.set()
    await processing
    assert runtime.store.get_handoff(handoff["id"])["result"] == {
        "status": "blocked",
        "reason": "task_terminal",
        "task_status": "CANCELLED",
        "role": "research",
    }
    assert not runtime.store.pending_inbox("research")
    runtime.store.close()


async def test_cancelled_task_does_not_flush_queued_handoff(tmp_path):
    runtime, _, handoff, _ = setup_runtime(tmp_path)
    task = runtime.store.get_task(handoff["task_id"])
    runtime.store.create_handoff(task["id"], "research", "qa", "Review", ["Pass"], task["version"])
    runtime.connection.target_names["qa"] = "QA"
    task = runtime.store.get_task(task["id"])
    runtime.store.transition_task(task["id"], "CANCELLED", task["version"])
    await runtime.flush_outbox()
    assert not runtime.switch.sent
    runtime.store.close()


async def test_durable_ack_result_replay_and_cursor(tmp_path):
    runtime, event, h, calls = setup_runtime(tmp_path)
    runtime.switch.stream = [{"type": "connection_state"}, event, event]
    await runtime.run()
    assert len(calls) == 1

    assert calls[0]["handoff_id"] == h["id"]
    assert runtime.store.get_handoff(h["id"])["result"]["status"] == "blocked"
    assert [p["type"] for p in runtime.switch.sent] == ["handoff.accepted", "handoff.completed"]
    assert runtime.store.get_cursor("research") == "4"
    await runtime.run()
    assert len(calls) == 1


async def test_prose_delivery_keeps_sender_checks_and_replay_identity(tmp_path):
    runtime, event, handoff, calls = setup_runtime(tmp_path)
    task = runtime.store.get_task(handoff["task_id"])
    item = runtime.store.pending_outbox()[0]
    event["payload"]["body"] = render_message(item, task, handoff)
    assert not await runtime.receive(
        {**event, "payload": {**event["payload"], "sender": "stranger"}}
    )
    assert await runtime.receive(event)
    await runtime.process_pending()
    assert not await runtime.receive(
        {**event, "payload": {**event["payload"], "message_id": "replay"}}
    )
    await runtime.process_pending()
    assert len(calls) == 1
    runtime.store.close()


@pytest.mark.parametrize("mutation", ["sender", "room", "task", "body", "proxy", "recipient"])
async def test_reject_untrusted_or_uncorrelated(tmp_path, mutation):
    runtime, event, h, calls = setup_runtime(tmp_path)
    if mutation == "sender":
        event["payload"]["sender"] = "stranger"
    elif mutation == "room":
        event["room_id"] = "elsewhere"
    elif mutation == "task":
        message = json.loads(event["payload"]["body"])
        message["task_id"] = "other"
        event["payload"]["body"] = json.dumps(message)
    elif mutation == "body":
        event["payload"]["body"] = "[]"
    elif mutation == "proxy":
        event["payload"]["on_behalf_of"] = "pm"
    else:
        runtime.connection.allowed_senders["participant-pm"] = "wrong-agent"
    assert not await runtime.receive(event)
    await runtime.process_pending()
    assert not calls
    assert runtime.store.get_handoff(h["id"])["status"] == "pending"


async def test_history_recovery_deduplicates_live_delivery(tmp_path):
    runtime, event, h, calls = setup_runtime(tmp_path)
    entry = {**event["payload"], "id": "history-id"}
    runtime.switch.stream = [
        {"type": "connection_state"},
        {
            "type": "gap",
            "resumed_at": 3,
            "history": {"room": [{"threads": [{"root": entry, "replies": []}]}]},
        },
        event,
    ]
    await runtime.run()
    assert len(calls) == 1
    assert runtime.store.get_cursor("research") == "4"


async def test_startup_recovers_offline_handoff_once_even_with_saved_cursor(tmp_path):
    runtime, event, handoff, calls = setup_runtime(tmp_path)
    entry = {**event["payload"], "id": "history-id"}
    reads = []

    async def recover_history():
        reads.append(True)
        return {"room": [{"threads": [{"root": entry, "replies": []}]}]}

    runtime.switch._recover_history = recover_history
    runtime.switch.stream = [{"type": "connection_state"}, event]
    runtime.store.set_cursor("research", 3)
    await runtime.run()
    assert len(calls) == 1 and len(reads) == 1
    assert runtime.store.get_handoff(handoff["id"])["status"] == "completed"
    restarted = AgentRuntime(runtime.connection, runtime.store, runtime.switch, runtime.graph)
    await restarted.run()
    assert len(calls) == 1 and len(reads) == 2


async def test_ack_delivery_failure_prevents_execution(tmp_path):
    runtime, event, h, calls = setup_runtime(tmp_path)

    async def unavailable(*args, **kwargs):
        return {"target_statuses": {"PM": "offline"}}

    runtime.switch.send_targeted_message = unavailable
    await runtime.receive(event)
    with pytest.raises(RuntimeError, match="not live"):
        await runtime.process_pending()
    assert not calls
    assert runtime.store.pending_inbox("research")


@pytest.mark.parametrize(
    "role",
    [
        "voice",
        "community",
        "pm",
        "research",
        "engineering",
        "qa",
        "release",
        "documentation",
        "knowledge",
        "onboarding",
        "moderation",
        "security",
        "scheduling",
        "analytics",
        "incident",
    ],
)
async def test_each_identity_executes_own_compiled_graph(tmp_path, role):
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
    from langgraph.graph.state import CompiledStateGraph

    from switchcrew.graphs import build_agent_graph

    store = Store(tmp_path / "workflow.db")
    task = store.create_task("Investigate", {}, ["Return evidence"])
    handoff = store.create_handoff(
        task["id"], "dispatcher", f"agent-{role}", "Investigate", ["Return evidence"], 0
    )
    connection = AgentConnection(
        role,
        f"agent-{role}",
        f"participant-{role}",
        role,
        f"key-{role}",
        "room",
        {"trusted": "dispatcher"},
        {"dispatcher": "Dispatcher"},
    )
    switch = Switch()
    switch.store = store
    switch.agent_id = connection.agent_id
    calls = []

    async def execute(state):
        assert switch.sent[0]["type"] == "handoff.accepted"
        assert state["agent_role"] == role
        calls.append(state)
        return {"status": "completed", "output": "Configured test executor"}

    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "checkpoints.db")) as saver:
        graph = build_agent_graph(role, executor=execute, checkpointer=saver)
        assert isinstance(graph, CompiledStateGraph)
        runtime = AgentRuntime(connection, store, switch, graph)
        event = {
            "type": "message",
            "room_id": "room",
            "payload": {
                "sender": "trusted",
                "message_id": "message",
                "body": json.dumps(
                    {"type": "handoff", "task_id": task["id"], "handoff_id": handoff["id"]}
                ),
            },
        }
        await runtime.receive(event)
        await runtime.process_pending()
        await runtime.flush_outbox()
        assert store.get_handoff(handoff["id"])["result"]["status"] == "completed"
        # Simulate process death after graph checkpoint but before inbox completion.
        store.connection.execute("UPDATE inbox SET status='pending'")
        await runtime.process_pending()
        assert len(calls) == 1
    store.close()


async def test_restart_reuses_graph_commit_before_handoff_result_commit(tmp_path):
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    from switchcrew.graphs import build_agent_graph

    runtime, event, handoff, _ = setup_runtime(tmp_path)
    calls = []

    async def execute(state):
        calls.append(state)
        return {"status": "completed"}

    checkpoint_path = str(tmp_path / "checkpoint.db")
    original_complete = runtime.store.complete_handoff

    def crash(*args):
        raise RuntimeError("Simulated death before handoff commit")

    async with AsyncSqliteSaver.from_conn_string(checkpoint_path) as saver:
        runtime.graph = build_agent_graph("research", executor=execute, checkpointer=saver)
        runtime.store.complete_handoff = crash
        await runtime.receive(event)
        with pytest.raises(RuntimeError, match="Simulated death"):
            await runtime.process_pending()
    assert runtime.store.get_handoff(handoff["id"])["status"] == "accepted"
    runtime.store.complete_handoff = original_complete
    async with AsyncSqliteSaver.from_conn_string(checkpoint_path) as saver:
        runtime.graph = build_agent_graph("research", executor=execute, checkpointer=saver)
        await runtime.process_pending()
    assert len(calls) == 1
    assert runtime.store.get_handoff(handoff["id"])["result"]["status"] == "completed"


@pytest.mark.parametrize("handoff_id", [[], {}, None, 0, ""])
async def test_malformed_assignment_id_cannot_crash_receiver(tmp_path, handoff_id):
    runtime, event, _, calls = setup_runtime(tmp_path)
    body = json.loads(event["payload"]["body"])
    body["handoff_id"] = handoff_id
    event["payload"]["body"] = json.dumps(body)
    assert not await runtime.receive(event)
    assert not calls


@pytest.mark.parametrize("status", ["CANCELLED", "COMPLETED"])
@pytest.mark.parametrize("already_accepted", [False, True])
async def test_terminal_task_blocks_initial_and_resumed_assignment(
    tmp_path, status, already_accepted
):
    runtime, event, handoff, calls = setup_runtime(tmp_path)
    await runtime.receive(event)
    if already_accepted:
        runtime.store.accept_handoff(handoff["id"], "research")
        await runtime.flush_outbox()
    # Simulate cancellation/completion by the coordinator before a pending resume.
    runtime.store.connection.execute(
        "UPDATE tasks SET status=? WHERE id=?", (status, handoff["task_id"])
    )
    await runtime.process_pending()
    result = runtime.store.get_handoff(handoff["id"])["result"]
    assert result == {
        "status": "blocked",
        "reason": "task_terminal",
        "task_status": status,
        "role": "research",
    }
    assert not calls
    assert not runtime.store.pending_inbox("research")


async def test_cancellation_during_ack_delivery_prevents_execution(tmp_path):
    runtime, event, handoff, calls = setup_runtime(tmp_path)
    original_send = runtime.switch.send_targeted_message

    async def send_then_cancel(*args, **kwargs):
        result = await original_send(*args, **kwargs)
        task = runtime.store.get_task(handoff["task_id"])
        runtime.store.transition_task(task["id"], "CANCELLED", task["version"])
        return result

    runtime.switch.send_targeted_message = send_then_cancel
    await runtime.receive(event)
    await runtime.process_pending()
    assert not calls
    assert runtime.store.get_handoff(handoff["id"])["result"]["reason"] == "task_terminal"


async def test_handoff_surfaces_safe_memory_diagnostics(tmp_path):
    from switchcrew.graphs import build_agent_graph
    from switchcrew.memory import MemoryResult

    runtime, event, handoff, _ = setup_runtime(tmp_path)

    class OfflineMemory:
        async def prime(self, *args):
            return MemoryResult(warning="Memory unavailable; persistence is unconfirmed.")

        async def recall(self, *args):
            return MemoryResult()

        async def observe(self, *args):
            return MemoryResult(warning="Memory unavailable; persistence is unconfirmed.")

    async def execute(state):
        return {"status": "completed"}

    runtime.graph = build_agent_graph("research", executor=execute, memory=OfflineMemory())
    await runtime.receive(event)
    await runtime.process_pending()
    result = runtime.store.get_handoff(handoff["id"])["result"]
    assert result["memory"]["persisted"] is False
    assert result["memory"]["status"] == "degraded"
    assert "memory_context" not in result
