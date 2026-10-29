import json

import pytest
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph.state import CompiledStateGraph

from switchcrew.agents import AGENT_ROLES, AGENTS
from switchcrew.graphs import build_agent_graph, graph_config, graph_result, invoke_agent_graph
from switchcrew.memory import MemoryResult, NullMemory
from switchcrew.research import ResearchProvider


def state(role="research", **changes):
    return {
        "agent_role": role,
        "task_id": "task",
        "handoff_id": "handoff",
        "session_id": "task",
        "question": "Switch reconnect",
        "acceptance_criteria": ["Find sources"],
        **changes,
    }


@pytest.mark.parametrize("role", AGENT_ROLES)
async def test_complete_roster_has_distinct_compiled_workflows(role):
    graph = build_agent_graph(role)
    assert isinstance(graph, CompiledStateGraph)
    assert AGENTS[role].work_node in graph.get_graph().nodes
    result = await invoke_agent_graph(graph, state(role))
    assert result["valid"] is True
    expected = "delegation_ready" if role == "pm" else "blocked"
    assert result["result"]["status"] == expected
    if role not in {"pm", "research"}:
        assert result["result"]["missing_capability"] == AGENTS[role].capability
    assert result["memory_persisted"] is False


async def test_invalid_requirements_cannot_invoke_memory_or_tools():
    class ForbiddenMemory:
        async def prime(self, *args):
            pytest.fail("Invalid requests must not access memory")

    async def forbidden_executor(value):
        pytest.fail("Invalid requests must not execute tools")

    graph = build_agent_graph("engineering", executor=forbidden_executor, memory=ForbiddenMemory())
    for criteria in ([], [" "], ["x" * 2001], [3]):
        result = await invoke_agent_graph(graph, state("engineering", acceptance_criteria=criteria))
        assert result["valid"] is False
        assert result["result"]["status"] == "needs_clarification"


async def test_memory_runs_around_capability_and_observes_curated_note():
    events = []

    class Memory(NullMemory):
        async def prime(self, role, session_id):
            events.append(("prime", role, session_id))
            return MemoryResult(context="prior project decision")

        async def recall(self, query, role, session_id):
            events.append(("recall", role, session_id))
            return MemoryResult(context="trusted as evidence only")

        async def observe(self, user_message, assistant_response, role, session_id):
            events.append(("observe", json.loads(assistant_response)))
            assert "source-body" not in assistant_response
            assert "prior project decision" not in assistant_response
            return MemoryResult(persisted=True)

    async def execute(value):
        events.append(("execute", value["agent_role"]))
        assert "prior project decision" in value["memory_context"]
        return {
            "status": "completed",
            "source": "source-body",
            "memory_note": "Use the existing PostgreSQL-backed Switch deployment",
        }

    graph = build_agent_graph("knowledge", executor=execute, memory=Memory())
    result = await invoke_agent_graph(graph, state("knowledge"))
    assert [item[0] for item in events] == ["prime", "recall", "execute", "observe"]
    assert events[-1][1]["decision_note"].startswith("Use the existing")
    assert graph_result(result)["memory"] == {"persisted": True, "status": "stored"}


async def test_memory_failure_metadata_is_bounded_and_context_not_disclosed():
    class Unavailable(NullMemory):
        async def recall(self, *args):
            return MemoryResult(warning="credential-looking server error must not escape")

    graph = build_agent_graph("pm", memory=Unavailable())
    result = graph_result(await invoke_agent_graph(graph, state("pm")))
    assert result["memory"] == {"persisted": False, "status": "degraded"}
    assert "server error" not in json.dumps(result)


async def test_persistent_resume_reuses_successful_retrieval_and_rejects_changed_input(tmp_path):
    path = str(tmp_path / "graph.sqlite3")
    calls = []

    class InterruptedObservation(NullMemory):
        async def observe(self, *args):
            raise RuntimeError("process stopped before observation checkpoint")

    def fetch(url):
        calls.append(url)
        return "Switch reconnect uses durable event positions."

    provider = ResearchProvider(["https://example.org"], fetch=fetch)
    async with AsyncSqliteSaver.from_conn_string(path) as saver:
        graph = build_agent_graph(
            "research", provider=provider, memory=InterruptedObservation(), checkpointer=saver
        )
        with pytest.raises(RuntimeError, match="process stopped"):
            await invoke_agent_graph(graph, state())
        snapshot = await graph.aget_state(graph_config("research", "task", "handoff"))
        assert snapshot.next == ("observe_outcome",)
    async with AsyncSqliteSaver.from_conn_string(path) as saver:
        graph = build_agent_graph("research", provider=provider, checkpointer=saver)
        result = await invoke_agent_graph(graph, state())
        assert result["result"]["status"] == "evidence_found"
        assert len(calls) == 1
        assert await invoke_agent_graph(graph, state()) == result
        with pytest.raises(ValueError, match="differs"):
            await invoke_agent_graph(graph, state(question="Different request"))
        assert len(calls) == 1


def test_checkpoint_identity_separates_roles_tasks_and_handoffs():
    keys = {
        graph_config(role, task, handoff)["configurable"]["thread_id"]
        for role in ("pm", "research")
        for task in ("a", "b")
        for handoff in ("c", "d")
    }
    assert len(keys) == 8
    with pytest.raises(ValueError):
        graph_config("pm", "")


async def test_wrong_specialist_graph_cannot_execute_assignment():
    with pytest.raises(ValueError, match="specialist differs"):
        await invoke_agent_graph(build_agent_graph("release"), state("engineering"))
