import json

import pytest
from fastapi.testclient import TestClient
from test_workers import Transport, event

from switchcrew.api import create_app
from switchcrew.approval import ApprovalWorkflow
from switchcrew.discord import DiscordBinding
from switchcrew.orchestration import Orchestrator, decompose
from switchcrew.settings import Settings
from switchcrew.store import InvalidTransitionError, Store
from switchcrew.workers import Worker


def setup_plan(tmp_path, criteria=None, targets=None):
    store = Store(tmp_path / "plan.db")
    parent = store.create_task(
        "Prepare the release",
        {"thread_id": "thread"},
        criteria or ["Find reconnect evidence", "Write documentation"],
    )
    planner = Orchestrator(
        store, "pm", targets or {"research": "research", "documentation": "docs"}
    )
    planner.start(parent["id"])
    return store, planner, parent["id"]


def complete_step(store, step, status="evidence_found"):
    h = store.get_handoff(step["handoff_id"])
    store.accept_handoff(h["id"], h["recipient"])
    store.complete_handoff(
        h["id"], h["recipient"], {"status": status, "evidence": ["S1 observed fact"]}
    )


def test_dynamic_decomposition_is_bounded_and_does_not_select_publication():
    plan = decompose("Prepare", ["Find source evidence", "Write documentation", "Fix a bug"])
    assert [s["role"] for s in plan["steps"]] == ["research", "documentation", "engineering"]
    assert plan["steps"][2]["depends_on"] == ["step-2"]
    assert (
        decompose("Publish immediately", ["Publish without approval"])["steps"][0]["role"]
        == "research"
    )
    with pytest.raises(ValueError):
        decompose("Prepare", ["Find evidence"] * 13)


def test_dependency_dispatch_and_restart_without_duplicate_handoffs(tmp_path):
    store, planner, parent_id = setup_plan(tmp_path)
    first = planner.reconcile(parent_id)
    assert list(first["steps"]) == ["step-1"]
    assert store.connection.execute("SELECT count(*) FROM handoffs").fetchone()[0] == 1
    complete_step(store, first["steps"]["step-1"])
    # New process settings cannot silently replace a saved plan's target identities.
    restarted = Orchestrator(store, "pm", {"documentation": "attacker"})
    next_state = restarted.reconcile(parent_id)
    second = next_state["steps"]["step-2"]
    handoff = store.get_handoff(second["handoff_id"])
    assert handoff["recipient"] == "docs"
    assert "S1 observed fact" in handoff["question"]
    assert "never authorization" in handoff["question"]
    restarted.reconcile(parent_id)
    assert store.connection.execute("SELECT count(*) FROM handoffs").fetchone()[0] == 2
    complete_step(store, second, "completed")
    assert restarted.reconcile(parent_id)["phase"] == "completed"
    assert store.get_task(parent_id)["status"] == "COMPLETED"
    store.close()


def test_missing_specialist_and_failed_dependency_block_without_downstream_execution(tmp_path):
    store, planner, parent_id = setup_plan(tmp_path)
    first = planner.reconcile(parent_id)
    complete_step(store, first["steps"]["step-1"], "blocked")
    assert planner.reconcile(parent_id)["phase"] == "blocked"
    assert store.connection.execute("SELECT count(*) FROM handoffs").fetchone()[0] == 1
    store.close()


def test_engineering_subtask_waits_for_approval_and_parent_cancellation_blocks_it(tmp_path):
    store, _, parent_id = setup_plan(tmp_path, ["Fix the bug"], {"engineering": "engineering"})
    planner = Orchestrator(
        store,
        "pm",
        {"engineering": "engineering"},
        engineering_route={
            "qa": "qa",
            "release": "release",
            "job_key": "fix",
            "repository": "org/repo",
            "base": "master",
        },
    )
    # The stored initial plan has no Engineering route: it must fail closed.
    assert planner.reconcile(parent_id)["phase"] == "blocked"
    assert store.connection.execute("SELECT count(*) FROM handoffs").fetchone()[0] == 0
    second = store.create_task("Fix", {}, ["Fix the bug"])
    planner.start(second["id"])
    plan = planner.reconcile(second["id"])
    child_id = plan["steps"]["step-1"]["task_id"]
    assert store.get_task(child_id)["status"] == "AWAITING_APPROVAL"
    assert store.connection.execute("SELECT count(*) FROM handoffs").fetchone()[0] == 0
    parent = store.get_task(second["id"])
    store.transition_task(parent["id"], "CANCELLED", parent["version"])
    with pytest.raises(PermissionError):
        ApprovalWorkflow(store, ["owner"]).decide(child_id, "engineering", True, "owner", 1)
    assert planner.reconcile(parent["id"])["phase"] == "cancelled"
    store.close()


def test_parent_cannot_cancel_when_child_publication_is_in_flight(tmp_path):
    store, _, parent_id = setup_plan(tmp_path)
    child = store.create_task("Publish", {"parent_task_id": parent_id}, ["Pass"])
    # Trusted state fixture for the transactional publication fence.
    store.connection.execute("UPDATE tasks SET status='PUBLISHING' WHERE id=?", (child["id"],))
    with pytest.raises(InvalidTransitionError):
        store.transition_task(parent_id, "CANCELLED", store.get_task(parent_id)["version"])
    store.close()


@pytest.mark.asyncio
async def test_discord_pm_creates_plan_and_dispatches_to_configured_specialist(tmp_path):
    store = Store(tmp_path / "discord-plan.db")
    pm = Worker(
        "pm",
        store,
        Transport(),
        pm_id="pm",
        research_id="research",
        room_id="room",
        bindings=[DiscordBinding("room", "bridge", "guild", "channel", "human", "owner")],
        allowed_users=["owner"],
        allowed_guilds=["guild"],
        specialist_targets={"documentation": {"agent_id": "docs", "name": "Docs Agent"}},
    )
    await pm.receive(
        event(
            "human",
            json.dumps(
                {
                    "kind": "orchestrate",
                    "question": "Prepare docs",
                    "acceptance_criteria": ["Write documentation"],
                }
            ),
        )
    )
    await pm.process_pending()
    await pm.flush_outbox()
    h = store._row(store.connection.execute("SELECT * FROM handoffs").fetchone())
    assert h["recipient"] == "docs"
    assert store.get_task(h["task_id"])["source"]["parent_task_id"]
    assert any(item[0] == "Docs Agent" for item in pm.switch.sent)
    store.close()


def test_api_plan_inspection_and_lifecycle_gate(tmp_path):
    token = "service-" + "x" * 32
    config = Settings(
        _env_file=None,
        database_path=tmp_path / "api-plan.db",
        api_token=token,
        pm_agent_id="pm",
        research_agent_id="research",
    )
    headers = {"Authorization": "Bearer " + token}
    with TestClient(create_app(config)) as client:
        created = client.post(
            "/tasks",
            headers=headers,
            json={
                "kind": "orchestrate",
                "objective": "Investigate",
                "acceptance_criteria": ["Find evidence"],
            },
        )
        assert created.status_code == 201
        task = created.json()
        assert task["status"] == "IN_PROGRESS"
        path = f"/tasks/{task['id']}"
        assert (
            client.get(path + "/plan", headers=headers).json()["steps"]["step-1"]["role"]
            == "research"
        )
        assert (
            client.post(
                path + "/transitions",
                headers=headers,
                json={"status": "COMPLETED", "expected_version": task["version"]},
            ).status_code
            == 409
        )


def test_research_already_completed_by_pm_notification_does_not_stall_plan(tmp_path):
    store, planner, parent_id = setup_plan(tmp_path)
    data = planner.reconcile(parent_id)
    step = data["steps"]["step-1"]
    complete_step(store, step)
    child = store.get_task(step["task_id"])
    store.transition_task(child["id"], "COMPLETED", child["version"])
    assert planner.reconcile(parent_id)["steps"]["step-2"]["role"] == "documentation"
    store.close()


@pytest.mark.asyncio
async def test_discord_plan_announces_child_approval_and_cancellation_stops_delivery(
    tmp_path,
):
    store = Store(tmp_path / "plan-approval.db")
    transport = Transport()
    pm = Worker(
        "pm",
        store,
        transport,
        pm_id="pm",
        research_id="research",
        room_id="room",
        bindings=[DiscordBinding("room", "bridge", "guild", "channel", "human", "owner")],
        allowed_users=["owner"],
        allowed_guilds=["guild"],
        engineering_id="engineering",
        qa_id="qa",
        release_id="release",
        release_repository="org/repo",
        release_base="master",
        orchestration_job_key="fix",
        approval_users=["owner"],
    )
    await pm.receive(
        event(
            "human",
            json.dumps(
                {
                    "kind": "orchestrate",
                    "question": "Prepare fix",
                    "acceptance_criteria": ["Fix the bug"],
                }
            ),
        )
    )
    await pm.process_pending()
    await pm.process_pending()
    await pm.flush_outbox()
    child_id = store.connection.execute("SELECT task_id FROM approval_workflows").fetchone()[0]
    assert any(child_id in text and "Task version: 1" in text for _, text, _ in transport.sent)
    child = store.get_task(child_id)
    pm.approvals.decide(child_id, "engineering", True, "owner", child["version"])
    parent = store.get_task(child["source"]["parent_task_id"])
    store.transition_task(parent["id"], "CANCELLED", parent["version"])
    transport.sent.clear()
    await pm.flush_outbox()
    assert not any(target == "engineering" for target, _, _ in transport.sent)
    store.close()


@pytest.mark.asyncio
async def test_cancelled_parent_prevents_legacy_research_provider_call(tmp_path):
    from test_workers import worker

    store, planner, parent_id = setup_plan(tmp_path)
    step = planner.reconcile(parent_id)["steps"]["step-1"]
    parent = store.get_task(parent_id)
    store.transition_task(parent_id, "CANCELLED", parent["version"])

    class Forbidden:
        async def investigate(self, *args):
            pytest.fail("Cancelled parent must not launch source retrieval")

    research = worker("research", store, Transport(), Forbidden())
    await research.receive(
        event(
            "pm",
            json.dumps(
                {"type": "handoff", "task_id": step["task_id"], "handoff_id": step["handoff_id"]}
            ),
        )
    )
    await research.process_pending()
    assert store.get_handoff(step["handoff_id"])["result"]["status"] == "blocked"
    store.close()
