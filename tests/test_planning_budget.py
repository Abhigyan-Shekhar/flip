import pytest

from switchcrew.harnesses import HarnessError, HarnessPlanner, HarnessRole
from switchcrew.orchestration import Orchestrator
from switchcrew.store import Store


class ControlledRunner:
    def __init__(self, output):
        self.output = output
        self.calls = 0

    async def run_json(self, spec, prompt):
        self.calls += 1
        if isinstance(self.output, Exception):
            raise self.output
        return self.output


def setup(tmp_path, output, **policy):
    runner = ControlledRunner(output)
    planner = HarnessPlanner(
        HarnessRole("pm", "codex", tmp_path),
        runner,
        available_roles={"pm", "research"},
        **policy,
    )
    store = Store(tmp_path / "tasks.sqlite3")
    orchestrator = Orchestrator(store, "pm", {"research": "agent-research"}, planner=planner)
    return store, orchestrator, runner


@pytest.mark.asyncio
async def test_one_model_call_budget_is_durable_even_when_model_fails(tmp_path):
    store, orchestration, runner = setup(tmp_path, HarnessError("offline"), max_calls=1)
    task = store.create_task("Explore", {}, ["Investigate"])
    with pytest.raises(HarnessError):
        await orchestration.start_with_planner(task["id"])
    with pytest.raises(ValueError, match="budget exhausted"):
        await orchestration.start_with_planner(task["id"])
    assert runner.calls == 1
    assert store.get_task(task["id"])["status"] == "DRAFT"
    store.close()


@pytest.mark.asyncio
async def test_explicit_rules_fallback_cannot_invent_specialists(tmp_path):
    store, orchestration, runner = setup(
        tmp_path, HarnessError("offline"), fallback_on_failure=True
    )
    task = store.create_task("Explore", {}, ["Investigate evidence"])
    plan = await orchestration.start_with_planner(task["id"])
    assert plan["spec"]["planner"] == "bounded_criterion_rules_explicit_fallback_v1"
    assert plan["spec"]["steps"][0]["role"] == "research"
    assert runner.calls == 1
    other = store.create_task("Write", {}, ["Write documentation"])
    with pytest.raises(ValueError, match="unconfigured role"):
        await orchestration.start_with_planner(other["id"])
    assert store.get_task(other["id"])["status"] == "DRAFT"
    store.close()


@pytest.mark.asyncio
async def test_planner_enforces_model_step_and_prompt_limits(tmp_path):
    store, orchestration, runner = setup(
        tmp_path,
        {"steps": [{"role": "research", "criterion": "A"}, {"role": "research", "criterion": "B"}]},
        max_steps=1,
    )
    task = store.create_task("Explore", {}, ["Investigate"])
    with pytest.raises(ValueError, match="one to twelve"):
        await orchestration.start_with_planner(task["id"])
    assert store.get_task(task["id"])["status"] == "DRAFT"
    assert runner.calls == 1
    store.close()


def test_invalid_policy_cannot_expand_limits(tmp_path):
    with pytest.raises(ValueError, match="bounded budgets"):
        HarnessPlanner(HarnessRole("pm", "codex", tmp_path), max_calls=100)
