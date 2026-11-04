import asyncio

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


@pytest.mark.asyncio
async def test_prompt_preflight_does_not_consume_model_budget(tmp_path):
    store, orchestration, runner = setup(
        tmp_path,
        {"steps": [{"role": "research", "criterion": "Investigate"}]},
        max_calls=1,
        max_prompt_chars=1000,
    )
    task = store.create_task("Explore " + "x" * 1100, {}, ["Investigate"])
    with pytest.raises(HarnessError, match="prompt exceeds"):
        await orchestration.start_with_planner(task["id"])
    assert runner.calls == 0
    assert (
        store.connection.execute(
            "SELECT attempts FROM harness_plan_attempts WHERE task_id=?", (task["id"],)
        ).fetchone()
        is None
    )

    # A corrected operator policy can retry the same draft with its full budget.
    orchestration.planner = HarnessPlanner(
        HarnessRole("pm", "codex", tmp_path),
        runner,
        available_roles={"pm", "research"},
        max_calls=1,
        max_prompt_chars=32000,
    )
    plan = await orchestration.start_with_planner(task["id"])
    assert plan["spec"]["planner"] == "harness_structured_v1"
    assert runner.calls == 1
    store.close()


@pytest.mark.asyncio
async def test_enabling_fallback_recovers_exhausted_model_calls(tmp_path):
    store, orchestration, runner = setup(tmp_path, HarnessError("offline"), max_calls=1)
    task = store.create_task("Explore", {}, ["Investigate evidence"])
    with pytest.raises(HarnessError):
        await orchestration.start_with_planner(task["id"])

    orchestration.planner = HarnessPlanner(
        HarnessRole("pm", "codex", tmp_path),
        runner,
        available_roles={"pm", "research"},
        max_calls=1,
        fallback_on_failure=True,
    )
    plan = await orchestration.start_with_planner(task["id"])
    assert plan["spec"]["planner"] == "bounded_criterion_rules_explicit_fallback_v1"
    assert runner.calls == 1
    assert (
        store.connection.execute(
            "SELECT attempts FROM harness_plan_attempts WHERE task_id=?", (task["id"],)
        ).fetchone()[0]
        == 1
    )
    store.close()


@pytest.mark.asyncio
async def test_concurrent_planners_share_persisted_plan_across_connections(tmp_path):
    class BlockingRunner:
        def __init__(self):
            self.calls = 0
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def run_json(self, spec, prompt):
            self.calls += 1
            self.started.set()
            await self.release.wait()
            return {"steps": [{"role": "research", "criterion": "Investigate"}]}

    runner = BlockingRunner()
    path = tmp_path / "shared.sqlite3"
    first_store = Store(path)
    task = first_store.create_task("Explore", {}, ["Investigate"])
    second_store = Store(path)

    def planner(store):
        return Orchestrator(
            store,
            "pm",
            {"research": "agent-research"},
            planner=HarnessPlanner(
                HarnessRole("pm", "codex", tmp_path),
                runner,
                available_roles={"pm", "research"},
                max_calls=2,
            ),
        )

    first = asyncio.create_task(planner(first_store).start_with_planner(task["id"]))
    await runner.started.wait()
    second = asyncio.create_task(planner(second_store).start_with_planner(task["id"]))
    await asyncio.sleep(0.05)
    assert not second.done()
    assert runner.calls == 1
    runner.release.set()
    first_plan, second_plan = await asyncio.gather(first, second)
    assert first_plan == second_plan
    assert (
        first_store.connection.execute(
            "SELECT attempts FROM harness_plan_attempts WHERE task_id=?", (task["id"],)
        ).fetchone()[0]
        == 1
    )
    assert (
        first_store.connection.execute("SELECT COUNT(*) FROM harness_plan_claims").fetchone()[0]
        == 0
    )
    first_store.close()
    second_store.close()


@pytest.mark.asyncio
async def test_expired_claim_can_be_recovered_after_crash(tmp_path):
    store, orchestration, runner = setup(
        tmp_path, {"steps": [{"role": "research", "criterion": "Investigate"}]}
    )
    task = store.create_task("Explore", {}, ["Investigate"])
    store.connection.execute(
        "INSERT INTO harness_plan_claims(task_id,token,expires_at) VALUES(?,?,?)",
        (task["id"], "crashed-worker", 0),
    )
    plan = await orchestration.start_with_planner(task["id"])
    assert plan["spec"]["planner"] == "harness_structured_v1"
    assert runner.calls == 1
    assert store.connection.execute("SELECT COUNT(*) FROM harness_plan_claims").fetchone()[0] == 0
    store.close()
