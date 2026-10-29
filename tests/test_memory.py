from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from switchcrew.memory import DEFAULT_PROJECT, NullMemory, WaggleMemory, memory_from_settings


def memory():
    instance = WaggleMemory(command=["waggle-server", "serve", "--transport", "stdio"])
    instance._invoke = AsyncMock(
        return_value=SimpleNamespace(
            isError=False, structuredContent={"summary": "Prior decision"}, content=[]
        )
    )
    return instance


async def test_scoped_tools_and_retrieval_bounds():
    instance = memory()
    await instance.prime("pm", "thread-1")
    instance._invoke.assert_awaited_with(
        "prime_context",
        {
            "project": DEFAULT_PROJECT,
        },
    )
    result = await instance.recall("prior choice", "research", "thread-1")
    assert "Prior decision" in result.context
    args = instance._invoke.call_args.args
    assert args[0] == "query_graph"
    assert args[1]["max_nodes"] == 10
    assert args[1]["max_depth"] == 1
    assert args[1]["retrieval_mode"] == "hybrid"
    assert "agent_id" not in args[1]
    assert "session_id" not in args[1]


async def test_observe_requires_server_confirmation():
    instance = memory()
    result = await instance.observe("Decision request", "Chosen design", "pm", "thread-1")
    assert not result.persisted and result.warning
    assert instance._invoke.call_args.args[0] == "observe_conversation"
    assert instance._invoke.call_args.args[1]["user_message"] == "Decision request"
    instance._invoke.return_value.structuredContent = {"verbatim_stored": True}
    assert (await instance.observe("Decision", "Result", "pm", "thread-1")).persisted


async def test_tool_error_and_unavailable_do_not_leak():
    instance = memory()
    instance._invoke.return_value.isError = True
    assert (await instance.prime("pm", "thread-1")).warning
    instance._invoke.side_effect = RuntimeError("secret-token-xyz")
    result = await instance.prime("pm", "thread-1")
    assert result.warning and "secret-token-xyz" not in result.warning
    assert not result.persisted


@pytest.mark.parametrize("text", ["token=abc123", "Bearer abc123", "password: hunter2"])
async def test_sensitive_content_never_sent(text):
    instance = memory()
    result = await instance.observe("Durable decision", text, "pm", "thread-1")
    assert result.warning and not result.persisted
    instance._invoke.assert_not_called()


async def test_explicit_token_and_sensitive_recall_output_omitted():
    instance = WaggleMemory(url="https://memory.example/mcp", token="unique-credential")
    instance._invoke = AsyncMock()
    assert (await instance.recall("unique-credential", "pm", "t")).warning
    instance._invoke.assert_not_called()
    instance._invoke.return_value = SimpleNamespace(
        isError=False, structuredContent={"summary": "token=abc123"}, content=[]
    )
    result = await instance.prime("pm", "t")
    assert not result.context and result.warning


async def test_disabled_does_not_connect_or_store():
    instance = memory_from_settings(SimpleNamespace(memory_enabled=False))
    assert isinstance(instance, NullMemory)
    assert not (await instance.observe("token=abc", "answer", "pm", "t")).persisted
    assert not (await instance.prime("pm", "t")).warning


async def test_empty_and_oversized_observations_skipped():
    instance = memory()
    assert not (await instance.observe("", "answer", "pm", "t")).persisted
    assert (await instance.observe("x" * 4001, "answer", "pm", "t")).warning
    instance._invoke.assert_not_called()


def test_invalid_transport_configuration():
    for kwargs in [
        {},
        {"command": ["cmd"], "url": "https://example.org"},
        {"url": "http://example.org"},
        {"url": "https://user:pass@example.org"},
    ]:
        with pytest.raises(ValueError):
            WaggleMemory(**kwargs)


async def test_real_sdk_stdio_roundtrip(tmp_path):
    """Exercise initialization, real MCP transport, serialization and structured result."""
    import sys

    server = tmp_path / "mcp_server.py"
    server.write_text("""from mcp.server.fastmcp import FastMCP
mcp = FastMCP("isolated-test")
@mcp.tool()
def prime_context(project: str) -> dict:
    return {"summary": project}
@mcp.tool()
def query_graph(query: str, project: str, max_nodes: int,
                max_depth: int, retrieval_mode: str) -> dict:
    return {"summary": query, "nodes": max_nodes, "depth": max_depth,
            "mode": retrieval_mode, "scope": project}
@mcp.tool()
def observe_conversation(user_message: str, assistant_response: str,
                         project: str, agent_id: str, session_id: str) -> dict:
    return {"verbatim_stored": True, "nodes_extracted": 1}
mcp.run(transport="stdio")
""")
    instance = WaggleMemory(command=[sys.executable, str(server)], timeout=10)
    prime = await instance.prime("pm", "wire-test")
    assert prime.warning is None
    assert DEFAULT_PROJECT in prime.context
    recall = await instance.recall("Verified choice", "research", "wire-test")
    assert recall.warning is None
    assert '"nodes": 10' in recall.context
    assert '"depth": 1' in recall.context
    result = await instance.observe("Decision", "Verified choice", "pm", "wire-test")
    assert result.persisted and result.warning is None


async def test_timeout_is_safe_and_bounded():
    import asyncio

    instance = memory()
    instance.timeout = 0.001

    async def blocked(*args):
        await asyncio.sleep(1)

    instance._invoke = blocked
    result = await instance.prime("pm", "t")
    assert result.warning == "Memory unavailable; persistence is unconfirmed."
    assert not result.persisted


async def test_cross_task_role_recall_uses_project_not_observation_provenance():
    instance = memory()
    await instance.observe("Decision", "Use durable handoffs", "pm", "old-task")
    observed = instance._invoke.call_args.args[1]
    assert observed["project"] == DEFAULT_PROJECT
    assert observed["agent_id"] == "switchcrew:pm"
    assert observed["session_id"] == "old-task"
    await instance.recall("handoff decision", "research", "new-task")
    queried = instance._invoke.call_args.args[1]
    assert queried["project"] == observed["project"]
    assert "agent_id" not in queried and "session_id" not in queried
    other = WaggleMemory(project="github.com/example/other", command=["server"])
    other._invoke = instance._invoke
    await other.prime("research", "another-task")
    assert other._invoke.call_args.args[1] == {"project": "github.com/example/other"}
