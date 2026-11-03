"""Incident routing through controlled Switch envelopes, not live-service evidence."""

import json

from test_workers import Transport, event

from switchcrew.discord import DiscordBinding
from switchcrew.store import Store
from switchcrew.workers import Worker


def pm_worker(store):
    return Worker(
        "pm",
        store,
        Transport(),
        pm_id="pm",
        research_id="research",
        room_id="room",
        bindings=[
            DiscordBinding("room", "bridge", "guild", "support", "human", "owner"),
            DiscordBinding("room", "bridge", "guild", "support", "human2", "helper"),
        ],
        allowed_guilds=["guild"],
        allowed_users=["owner", "helper"],
        incident_channels=["support"],
        engineering_id="engineering",
        qa_id="qa",
        reviewer_id="reviewer",
        release_id="release",
        release_repository="org/repo",
        release_base="master",
        approval_users=["owner"],
        orchestration_job_key="fix",
        specialist_targets={
            "incident": {"agent_id": "incident", "name": "incident"},
            "documentation": {"agent_id": "docs", "name": "docs"},
        },
    )


async def test_natural_report_plan_multiple_humans_and_engineering_approval(tmp_path):
    store = Store(tmp_path / "incident.db")
    pm = pm_worker(store)
    incoming = event("human", "Synthetic fixture: Payments are failing for some users.", "report1")
    await pm.receive(incoming)
    await pm.process_pending()
    row = store.connection.execute("SELECT task_id FROM incidents").fetchone()
    assert row is not None
    parent_id = row[0]
    plan = pm.orchestrator.get(parent_id)
    assert plan["phase"] == "running"
    first = list(plan["steps"].values())
    assert {s["role"] for s in first} == {"incident", "research"}
    assert not store.connection.execute("SELECT task_id FROM approval_workflows").fetchall()
    await pm.flush_outbox()
    assert {target for target, _, _ in pm.switch.sent if target != "discord"} >= {
        "incident",
        "research",
    }
    await pm.receive(event("human2", "Any update?", "followup1"))
    await pm.process_pending()
    assert store.connection.execute("SELECT count(*) FROM incidents").fetchone()[0] == 1
    assert store.connection.execute("SELECT count(*) FROM incident_sources").fetchone()[0] == 2
    # Controlled investigation evidence; this test does not claim a live diagnosis.
    for step in first:
        handoff = store.get_handoff(step["handoff_id"])
        store.accept_handoff(handoff["id"], handoff["recipient"])
        store.complete_handoff(
            handoff["id"],
            handoff["recipient"],
            {
                "status": "completed" if step["role"] == "incident" else "evidence_found",
                "evidence": ["synthetic fixture investigation; production cause unverified"],
            },
        )
    await pm.process_pending()
    plan = pm.orchestrator.get(parent_id)
    engineering = next(s for s in plan["steps"].values() if s["role"] == "engineering")
    child = store.get_task(engineering["task_id"])
    assert child["status"] == "AWAITING_APPROVAL"
    assert (
        store.connection.execute(
            "SELECT count(*) FROM handoffs WHERE recipient='engineering'"
        ).fetchone()[0]
        == 0
    )
    decision = {
        "type": "approval",
        "task_id": child["id"],
        "stage": "engineering",
        "approved": True,
        "expected_version": child["version"],
    }
    await pm.receive(event("human2", json.dumps(decision), "bad-approval"))
    await pm.process_pending()
    assert store.get_task(child["id"])["status"] == "AWAITING_APPROVAL"
    await pm.receive(event("human", json.dumps(decision), "human-approval"))
    await pm.process_pending()
    assert store.get_task(child["id"])["status"] == "IN_PROGRESS"
    await pm.receive(event("human", "/crew team " + parent_id, "team-command"))
    await pm.process_pending()
    await pm.flush_outbox()
    assert any("Team:" in text for _, text, _ in pm.switch.sent)
    await pm.receive(event("human", "/crew cancel " + parent_id, "cancel-command"))
    await pm.process_pending()
    assert store.get_task(parent_id)["status"] == "CANCELLED"
    store.close()


async def test_replay_after_incident_creation_before_plan_recovers_without_new_task(tmp_path):
    from switchcrew.discord import normalize_discord_event

    store = Store(tmp_path / "replay.db")
    pm = pm_worker(store)
    report = event("human", "Checkout is broken, synthetic fixture", "report2")
    normalized = normalize_discord_event(
        report, pm.bindings, allowed_guilds={"guild"}, allowed_users={"owner"}
    )
    created = pm.incidents.ingest(normalized)
    assert pm.orchestrator.get(created["task_id"]) is None
    # Simulate the next PM invocation after committed incident intake.
    await pm.receive(report)
    await pm.process_pending()
    assert pm.orchestrator.get(created["task_id"]) is not None
    assert store.connection.execute("SELECT count(*) FROM incidents").fetchone()[0] == 1
    store.close()


async def test_budget_and_chatter_do_not_fall_through_to_new_tasks(tmp_path):
    store = Store(tmp_path / "budget.db")
    pm = pm_worker(store)
    pm.incidents.max_events_per_minute = 1
    await pm.receive(event("human", "Synthetic fixture: payments failing", "one"))
    await pm.process_pending()
    before = store.connection.execute("SELECT count(*) FROM tasks").fetchone()[0]
    for mid, text in [
        ("two", "Synthetic fixture: api crashing"),
        ("three", "hello everyone"),
        ("four", "/crew unsupported anything"),
    ]:
        await pm.receive(event("human", text, mid))
        await pm.process_pending()
    assert store.connection.execute("SELECT count(*) FROM tasks").fetchone()[0] == before
    assert store.connection.execute("SELECT count(*) FROM incidents").fetchone()[0] == 1
    assert any("/crew" in text for _, text, _ in pm.switch.sent)
    store.close()


async def test_status_reply_replay_reuses_first_outbox_after_task_advances(tmp_path, monkeypatch):
    import pytest

    store = Store(tmp_path / "reply-replay.db")
    pm = pm_worker(store)
    task = store.create_task(
        "Inspect", {"room_id": "room", "thread_id": "thread", "user_id": "owner"}, ["Investigate"]
    )
    task = store.transition_task(task["id"], "PLANNED", task["version"])
    task = store.transition_task(task["id"], "IN_PROGRESS", task["version"])
    command = event("human", "/crew status " + task["id"], "inspect-once")
    await pm.receive(command)
    complete = store.complete_inbox

    def crash(*_args):
        raise RuntimeError("crash after durable reply")

    monkeypatch.setattr(store, "complete_inbox", crash)
    with pytest.raises(RuntimeError, match="durable reply"):
        await pm.process_pending()
    before = [
        o for o in store.pending_outbox() if o["payload"].get("type") == "crew_status_inspect-once"
    ]
    assert len(before) == 1 and "IN_PROGRESS" in before[0]["payload"]["text"]
    store.transition_task(task["id"], "CANCELLED", task["version"])
    monkeypatch.setattr(store, "complete_inbox", complete)
    await pm.process_pending()
    after = [
        o for o in store.pending_outbox() if o["payload"].get("type") == "crew_status_inspect-once"
    ]
    assert after == before
    assert not store.pending_inbox("pm")
    store.close()
