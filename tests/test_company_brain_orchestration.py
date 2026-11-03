import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from switchcrew.approval import ApprovalWorkflow
from switchcrew.company_brain import BrainResult
from switchcrew.discord import DiscordBinding
from switchcrew.orchestration import Orchestrator
from switchcrew.settings import Settings
from switchcrew.store import Store
from switchcrew.workers import Worker

ROUTE = {
    "qa": "qa",
    "reviewer": "reviewer",
    "release": "release",
    "job_key": "fix",
    "repository": "org/repo",
    "base": "main",
}


class Brain:
    max_results = 4

    def __init__(self, *, result=None, wait=None, store=None):
        self.result = result or BrainResult(
            context={"repo_root": "/repo", "revision": "a" * 40, "evidence": [{"source_uid": "n1"}]}
        )
        self.wait = wait
        self.store = store
        self.calls = []

    async def get_context(self, query, max_results=5):
        self.calls.append(("get_context", query, max_results))
        return await self._result()

    async def check_change(self, files, symbols=None):
        self.calls.append(("check_change", files, symbols))
        return await self._result()

    async def _result(self):
        if self.store is not None:
            assert not self.store.connection.in_transaction
        if self.wait is not None:
            self.wait[0].set()
            await self.wait[1].wait()
        return self.result


def engineer_plan(store, brain):
    task = store.create_task(
        "Implement the requested fix",
        {"room_id": "room"},
        ["Implement src/app.py"],
        task_id="parent",
    )
    planner = Orchestrator(
        store,
        "pm",
        {"engineering": "engineering"},
        engineering_route=ROUTE,
        company_brain=brain,
    )
    planner.start(task["id"])
    return planner, task["id"]


@pytest.mark.asyncio
async def test_sync_reconcile_defers_brain_engineering_then_async_persists_snapshot(tmp_path):
    store = Store(tmp_path / "orchestrated.db")
    brain = Brain(store=store)
    planner, task_id = engineer_plan(store, brain)

    sync_state = planner.reconcile(task_id)
    assert sync_state["steps"] == {}
    assert not store.connection.execute("SELECT 1 FROM approval_workflows").fetchone()
    assert brain.calls == []

    state = await planner.reconcile_with_context(task_id)
    step = state["steps"]["step-1"]
    child = store.get_task(step["task_id"])
    data = planner.get(task_id)
    approval = ApprovalWorkflow(store, ["human"]).get(child["id"])
    assert child["status"] == "AWAITING_APPROVAL"
    assert approval["phase"] == "awaiting_engineering_approval"
    assert approval["spec"]["company_brain"] == {
        "context": brain.result.context,
        "warning": None,
    }
    assert data["company_brain_snapshots"][child["id"]] == approval["spec"]["company_brain"]
    assert brain.calls == [("check_change", ["src/app.py"], None)]
    assert not store.get_handoff(child["id"])

    restarted_store = Store(tmp_path / "orchestrated.db")
    restarted_brain = Brain()
    restarted = Orchestrator(
        restarted_store,
        "pm",
        {"engineering": "engineering"},
        engineering_route=ROUTE,
        company_brain=restarted_brain,
    )
    await restarted.reconcile_with_context(task_id)
    assert restarted_brain.calls == []
    assert restarted.get(task_id)["company_brain_snapshots"] == data["company_brain_snapshots"]
    restarted_store.close()
    store.close()


@pytest.mark.asyncio
async def test_concurrent_reconcile_waiters_share_one_child_snapshot(tmp_path):
    store = Store(tmp_path / "concurrent.db")
    entered, release = asyncio.Event(), asyncio.Event()
    brain = Brain(wait=(entered, release), store=store)
    planner, task_id = engineer_plan(store, brain)

    first = asyncio.create_task(planner.reconcile_with_context(task_id))
    await entered.wait()
    second = asyncio.create_task(planner.reconcile_with_context(task_id))
    await asyncio.sleep(0)
    release.set()
    await asyncio.gather(first, second)

    assert len(brain.calls) == 1
    plan = planner.get(task_id)
    child_id = plan["steps"]["step-1"]["task_id"]
    assert len(plan["company_brain_snapshots"]) == 1
    assert plan["company_brain_snapshots"][child_id]["context"] == brain.result.context
    store.close()


@pytest.mark.asyncio
async def test_incident_engineering_waits_for_dependencies_and_brain_warning_is_failsoft(tmp_path):
    store = Store(tmp_path / "incident.db")
    brain = Brain(result=BrainResult(context="", warning="Index unavailable"), store=store)
    parent = store.create_task("Respond to incident", {}, ["incident response"], task_id="incident")
    planner = Orchestrator(
        store,
        "pm",
        {"incident": "incident-agent", "research": "research-agent", "engineering": "eng"},
        engineering_route=ROUTE,
        company_brain=brain,
    )
    incident = {
        "category": "bug",
        "severity": "SEV2",
        "summary": "Fix src/app.py regression",
        "evidence_refs": [],
        "needs_engineering": True,
    }
    planner.start_incident(parent["id"], incident)
    first = await planner.reconcile_with_context(parent["id"])
    assert set(first["steps"]) == {"step-1", "step-2"}
    assert brain.calls == []
    for step in first["steps"].values():
        handoff = store.get_handoff(step["handoff_id"])
        store.accept_handoff(handoff["id"], handoff["recipient"])
        store.complete_handoff(
            handoff["id"], handoff["recipient"], {"status": "evidence_found", "evidence": []}
        )

    state = await planner.reconcile_with_context(parent["id"])
    engineering = next(entry for entry in state["steps"].values() if entry["role"] == "engineering")
    child = store.get_task(engineering["task_id"])
    approval = ApprovalWorkflow(store, ["human"]).get(child["id"])
    assert child["status"] == "AWAITING_APPROVAL"
    assert approval["spec"]["company_brain"]["warning"] == "Index unavailable"
    assert len(brain.calls) == 1
    store.close()


@pytest.mark.asyncio
async def test_cancellation_during_brain_retrieval_never_creates_engineering_approval(tmp_path):
    store = Store(tmp_path / "cancel.db")
    entered, release = asyncio.Event(), asyncio.Event()
    brain = Brain(wait=(entered, release), store=store)
    planner, task_id = engineer_plan(store, brain)

    pending = asyncio.create_task(planner.reconcile_with_context(task_id))
    await entered.wait()
    parent = store.get_task(task_id)
    store.transition_task(task_id, "CANCELLED", parent["version"])
    release.set()
    result = await pending
    assert result["phase"] == "cancelled"
    assert not store.connection.execute("SELECT 1 FROM approval_workflows").fetchone()
    assert not store.connection.execute("SELECT 1 FROM handoffs").fetchone()
    store.close()


@pytest.mark.asyncio
async def test_direct_pm_engineering_cancellation_during_brain_lookup_does_not_crash(tmp_path):
    store = Store(tmp_path / "direct-cancel.db")
    entered, release = asyncio.Event(), asyncio.Event()
    brain = Brain(wait=(entered, release), store=store)

    class Switch:
        connected = True

    worker = Worker(
        "pm",
        store,
        Switch(),
        pm_id="pm",
        research_id="research",
        room_id="room",
        bindings=[DiscordBinding("room", "bridge", "guild", "channel", "human", "owner")],
        allowed_guilds=["guild"],
        allowed_users=["owner"],
        engineering_id="engineering",
        qa_id="qa",
        reviewer_id="reviewer",
        release_id="release",
        approval_users=["owner"],
        release_repository="org/repo",
        release_base="main",
        company_brain=brain,
    )
    request = {
        "kind": "engineering",
        "job_key": "fix",
        "question": "Implement src/app.py",
        "acceptance_criteria": ["Verify src/app.py"],
    }
    event = {
        "type": "message",
        "room_id": "room",
        "bridge_id": "bridge",
        "payload": {
            "sender": "human",
            "message_id": "request-1",
            "thread_id": "thread-1",
            "body": json.dumps(request),
        },
    }
    pending = asyncio.create_task(worker._request(event))
    await entered.wait()
    task = store._row(store.connection.execute("SELECT * FROM tasks").fetchone())
    assert task["status"] == "DRAFT"
    store.transition_task(task["id"], "CANCELLED", task["version"])
    release.set()
    await pending

    assert store.get_task(task["id"])["status"] == "CANCELLED"
    assert not worker.approvals.get(task["id"])
    assert not store.get_handoff(task["id"])
    store.close()


@pytest.mark.asyncio
async def test_direct_pm_replay_after_approval_commit_restores_missing_prompt(tmp_path):
    store = Store(tmp_path / "direct-replay.db")
    brain = Brain(store=store)

    class Switch:
        connected = True

    worker = Worker(
        "pm",
        store,
        Switch(),
        pm_id="pm",
        research_id="research",
        room_id="room",
        bindings=[DiscordBinding("room", "bridge", "guild", "channel", "human", "owner")],
        allowed_guilds=["guild"],
        allowed_users=["owner"],
        engineering_id="engineering",
        qa_id="qa",
        reviewer_id="reviewer",
        release_id="release",
        approval_users=["owner"],
        release_repository="org/repo",
        release_base="main",
        company_brain=brain,
    )
    request = {
        "kind": "engineering",
        "job_key": "fix",
        "question": "Implement src/app.py",
        "acceptance_criteria": ["Verify src/app.py"],
    }
    event = {
        "type": "message",
        "room_id": "room",
        "bridge_id": "bridge",
        "payload": {
            "sender": "human",
            "message_id": "request-replay",
            "thread_id": "thread-replay",
            "body": json.dumps(request),
        },
    }
    reply = worker._reply

    def fail_after_approval(*args, **kwargs):
        raise RuntimeError("simulated prompt enqueue gap")

    worker._reply = fail_after_approval
    with pytest.raises(RuntimeError, match="prompt enqueue gap"):
        await worker._request(event)
    task = store._row(store.connection.execute("SELECT * FROM tasks").fetchone())
    assert task["status"] == "AWAITING_APPROVAL"
    persisted = worker.approvals.get(task["id"])["spec"]["company_brain"]

    worker._reply = reply
    await worker._request(event)
    assert worker.approvals.get(task["id"])["spec"]["company_brain"] == persisted
    assert len(brain.calls) == 1
    assert any(row["task_id"] == task["id"] for row in store.pending_outbox())

    approval = worker.approvals.get(task["id"])
    worker.approvals.decide(
        task["id"], "engineering", True, "owner", store.get_task(task["id"])["version"]
    )
    emitted = []
    worker._reply = lambda *args, **kwargs: emitted.append(args)
    await worker._request(event)
    assert emitted == []
    assert worker.approvals.get(task["id"])["phase"] == "engineering"
    assert approval["phase"] == "awaiting_engineering_approval"
    store.close()


def test_disabled_sync_orchestration_keeps_legacy_engineering_dispatch(tmp_path):
    store = Store(tmp_path / "disabled.db")
    task = store.create_task("Implement feature", {}, ["Implement src/app.py"])
    planner = Orchestrator(store, "pm", {"engineering": "engineering"}, engineering_route=ROUTE)
    planner.start(task["id"])
    state = planner.reconcile(task["id"])
    assert state["steps"]["step-1"]["role"] == "engineering"
    assert state["steps"]["step-1"]["status"] == "pending"
    assert ApprovalWorkflow(store).get(state["steps"]["step-1"]["task_id"])["phase"] == (
        "awaiting_engineering_approval"
    )
    store.close()


def test_api_uses_async_orchestration_context_and_settings(monkeypatch, tmp_path):
    from switchcrew import company_brain
    from switchcrew.api import create_app

    brain = Brain()
    monkeypatch.setattr(company_brain, "company_brain_from_settings", lambda settings: brain)
    settings = Settings(
        _env_file=None,
        database_path=tmp_path / "api.db",
        api_token="s" * 40,
        company_brain_enabled=True,
        company_brain_repo_root="/repo",
        company_brain_revision="a" * 40,
        company_brain_command=["unused-test-server"],
        pm_agent_id="pm",
        engineering_agent_id="engineering",
        qa_agent_id="qa",
        reviewer_agent_id="reviewer",
        release_agent_id="release",
        release_repository="org/repo",
        release_base="main",
        orchestration_job_key="fix",
    )
    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/tasks",
            headers={"Authorization": "Bearer " + "s" * 40},
            json={
                "kind": "orchestrate",
                "objective": "Implement the requested fix",
                "acceptance_criteria": ["Implement src/app.py"],
            },
        )
        assert response.status_code == 201
        task_id = response.json()["id"]
        plan = client.get(f"/tasks/{task_id}/plan", headers={"Authorization": "Bearer " + "s" * 40})
        assert plan.status_code == 200
        child_id = plan.json()["steps"]["step-1"]["task_id"]
        approval = client.app.state.approvals.get(child_id)
        assert approval["spec"]["company_brain"]["context"] == brain.result.context
        assert len(brain.calls) == 1


def test_api_redacts_company_brain_credential_from_responses(tmp_path):
    from switchcrew.api import create_app

    credential = "private-graph-credential"
    config = Settings(
        _env_file=None,
        database_path=tmp_path / "redaction.db",
        api_token="s" * 40,
        company_brain_token=credential,
    )
    with TestClient(create_app(config)) as client:
        response = client.post(
            "/tasks",
            headers={"Authorization": "Bearer " + "s" * 40},
            json={"objective": credential, "acceptance_criteria": ["Find relevant source"]},
        )
        assert response.status_code == 201
        assert credential not in response.text
        assert response.json()["objective"] == "[REDACTED]"
