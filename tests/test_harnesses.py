"""Harness v1 uses controlled local CLIs; no provider login or paid API calls."""

import json
import os
from pathlib import Path

import pytest

from switchcrew.graphs import build_agent_graph, invoke_agent_graph
from switchcrew.harnesses import (
    HarnessError,
    HarnessExecutor,
    HarnessPlanner,
    HarnessRole,
    HarnessRunner,
    load_harness_config,
)
from switchcrew.orchestration import Orchestrator
from switchcrew.organization_runtime import configured_executor
from switchcrew.store import Store


class ControlledRunner:
    def __init__(self, response):
        self.response = response
        self.prompts = []

    async def run_json(self, spec, prompt):
        self.prompts.append((spec, prompt))
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def config_file(tmp_path, roles):
    path = tmp_path / "private-harness.json"
    path.write_text(json.dumps({"roles": roles}))
    path.chmod(0o600)
    return path


def test_operator_config_rejects_privilege_and_public_permissions(tmp_path):
    path = config_file(tmp_path, {"research": {"provider": "codex", "workspace": str(tmp_path)}})
    assert load_harness_config(path)["research"].provider == "codex"
    path.chmod(0o644)
    with pytest.raises(PermissionError):
        load_harness_config(path)
    path = config_file(tmp_path, {"engineering": {"provider": "codex", "workspace": str(tmp_path)}})
    with pytest.raises(ValueError, match="nonprivileged"):
        load_harness_config(path)
    path = config_file(tmp_path, {"research": {"provider": "unknown", "workspace": str(tmp_path)}})
    with pytest.raises(ValueError, match="Supported"):
        load_harness_config(path)


def test_generic_specialist_capability_uses_operator_selected_harness(tmp_path):
    path = config_file(
        tmp_path,
        {"documentation": {"provider": "antigravity", "workspace": str(tmp_path)}},
    )
    settings = type("SettingsStub", (), {"harness_config": path})()
    executor = configured_executor("documentation", {"mode": "harness"}, None, None, settings)
    assert isinstance(executor, HarnessExecutor)
    assert executor.spec.provider == "antigravity"
    with pytest.raises(ValueError, match="No operator-configured"):
        configured_executor("security", {"mode": "harness"}, None, None, settings)


@pytest.mark.asyncio
async def test_research_graph_uses_harness_and_untrusted_memory(tmp_path):
    runner = ControlledRunner(
        {
            "status": "evidence_found",
            "summary": "Compared both sources",
            "artifact": "# Analysis",
            "evidence": ["S1:10"],
            "limitations": ["Requires review"],
        }
    )
    spec = HarnessRole("research", "codex", tmp_path)
    graph = build_agent_graph("research", executor=HarnessExecutor(spec, runner=runner))
    result = await invoke_agent_graph(
        graph,
        {
            "agent_role": "research",
            "task_id": "t1",
            "handoff_id": "h1",
            "question": "Compare the incidents",
            "acceptance_criteria": ["Find differences"],
        },
    )
    assert result["result"]["summary"] == "Compared both sources"
    assert result["result"]["trust"] == "model_generated_requires_human_review"
    assert "Compare the incidents" in runner.prompts[0][1]


@pytest.mark.asyncio
async def test_harness_failure_blocks_instead_of_using_deterministic_fallback(tmp_path):
    spec = HarnessRole("documentation", "antigravity", tmp_path)
    executor = HarnessExecutor(spec, runner=ControlledRunner(HarnessError("Unavailable")))
    result = await executor({"question": "Write notes", "acceptance_criteria": ["Cite sources"]})
    assert result["status"] == "blocked"
    assert "Unavailable" not in json.dumps(result)
    invalid = HarnessExecutor(spec, runner=ControlledRunner({"status": "completed"}))
    assert (await invalid({"question": "A", "acceptance_criteria": ["B"]}))["status"] == "blocked"


@pytest.mark.asyncio
async def test_model_plan_is_persisted_and_cannot_select_unconfigured_privilege(tmp_path):
    store = Store(tmp_path / "state.sqlite3")
    task = store.create_task("Write a reconnect guide", {}, ["Research", "Document"])
    runner = ControlledRunner(
        {
            "steps": [
                {"role": "research", "criterion": "Find authoritative source"},
                {"role": "documentation", "criterion": "Draft guide with citations"},
            ]
        }
    )
    planner = HarnessPlanner(HarnessRole("pm", "codex", tmp_path), runner)
    orchestration = Orchestrator(
        store, "pm", {"research": "research", "documentation": "docs"}, planner=planner
    )
    first = await orchestration.start_with_planner(task["id"])
    second = await orchestration.start_with_planner(task["id"])
    assert first == second
    assert first["spec"]["planner"] == "harness_structured_v1"
    assert len(runner.prompts) == 1
    assert [s["role"] for s in first["spec"]["steps"]] == ["research", "documentation"]
    assert first["spec"]["steps"][1]["depends_on"] == ["step-1"]

    attack = store.create_task("Unsafe", {}, ["Investigate"])
    runner.response = {"steps": [{"role": "engineering", "criterion": "Deploy immediately"}]}
    with pytest.raises(ValueError, match="unconfigured"):
        await orchestration.start_with_planner(attack["id"])
    assert store.get_task(attack["id"])["status"] == "DRAFT"
    assert store.get_handoff("step-1") is None
    store.close()


@pytest.mark.asyncio
async def test_model_planner_limits_roles_to_installed_harnesses(tmp_path):
    store = Store(tmp_path / "plans.sqlite3")
    task = store.create_task("Summarize", {}, ["Summarize useful results"])
    runner = ControlledRunner({"steps": [{"role": "documentation", "criterion": "Draft"}]})
    planner = HarnessPlanner(
        HarnessRole("pm", "codex", tmp_path),
        runner,
        available_roles={"pm", "research"},
    )
    orchestrator = Orchestrator(
        store,
        "pm",
        {"research": "research-agent", "documentation": "docs-agent"},
        planner=planner,
    )
    with pytest.raises(ValueError, match="unconfigured"):
        await orchestrator.start_with_planner(task["id"])
    assert store.get_task(task["id"])["status"] == "DRAFT"
    assert '"allowed_roles": ["research"]' in runner.prompts[0][1]
    store.close()


def _fake_cli(tmp_path, name, source):
    path = tmp_path / name
    path.write_text("#!/usr/bin/env python3\n" + source)
    path.chmod(0o700)
    return path


@pytest.mark.asyncio
async def test_local_codex_adapter_reads_final_message_and_strips_service_credentials(
    tmp_path, monkeypatch
):
    _fake_cli(
        tmp_path,
        "codex",
        "import json, os, pathlib, sys\n"
        "assert 'SWITCHCREW_API_TOKEN' not in os.environ\n"
        "assert '--sandbox' in sys.argv and 'read-only' in sys.argv\n"
        "assert 'hello' in sys.stdin.read()\n"
        "dest = pathlib.Path(sys.argv[sys.argv.index('--output-last-message')+1])\n"
        "dest.write_text(json.dumps({'ok': True}))\n",
    )
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("SWITCHCREW_API_TOKEN", "a-sensitive-demo-token")
    result = await HarnessRunner().run_json(HarnessRole("research", "codex", tmp_path), "hello")
    assert result == {"ok": True}


@pytest.mark.asyncio
async def test_local_antigravity_adapter_restricts_terminal(tmp_path, monkeypatch):
    _fake_cli(
        tmp_path,
        "agy",
        "import json, sys\n"
        "assert '--sandbox' in sys.argv and '--print' in sys.argv\n"
        "assert 'hello' in sys.argv\n"
        "print(json.dumps({'ok': True}))\n",
    )
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])
    result = await HarnessRunner().run_json(
        HarnessRole("documentation", "antigravity", Path(tmp_path)), "hello"
    )
    assert result == {"ok": True}


@pytest.mark.asyncio
async def test_invalid_cli_output_fails_closed(tmp_path, monkeypatch):
    _fake_cli(tmp_path, "agy", "print('This is not structured JSON')\n")
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])
    with pytest.raises(HarnessError, match="JSON object"):
        await HarnessRunner().run_json(HarnessRole("research", "antigravity", tmp_path), "hi")
    with pytest.raises(HarnessError, match="prompt exceeds"):
        await HarnessRunner().run_json(
            HarnessRole("research", "antigravity", tmp_path), "x" * 32001
        )
