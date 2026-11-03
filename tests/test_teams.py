import pytest

from switchcrew.orchestration import Orchestrator
from switchcrew.store import Store


def incident(**updates):
    return {
        "category": "service_failure",
        "severity": "SEV2",
        "summary": "API latency",
        "needs_engineering": False,
        "evidence_refs": ["log:123"],
        **updates,
    }


def test_incident_plan_is_durable_idempotent_and_readonly_work_parallel(tmp_path):
    store = Store(tmp_path / "incident.db")
    parent = store.create_task("Respond", {}, ["incident response"])
    planner = Orchestrator(
        store,
        "pm",
        {"incident": "incident-agent", "research": "research-agent", "documentation": "docs-agent"},
    )
    first = planner.start_incident(parent["id"], incident())
    assert first == planner.start_incident(parent["id"], incident())
    assert first["spec"]["steps"][-1]["depends_on"] == ["step-1", "step-2"]
    dispatched = planner.reconcile(parent["id"])
    assert {v["role"] for v in dispatched["steps"].values()} == {"incident", "research"}
    assert store.connection.execute("SELECT count(*) FROM handoffs").fetchone()[0] == 2
    store.close()


def test_incident_engineering_keeps_approval_boundary_and_requires_route(tmp_path):
    store = Store(tmp_path / "engineering.db")
    parent = store.create_task("Respond", {}, ["incident response"])
    targets = {"incident": "i", "research": "r", "engineering": "e"}
    planner = Orchestrator(
        store, "pm", targets, engineering_route={"qa": "q", "reviewer": "review", "release": "rel"}
    )
    data = planner.start_incident(parent["id"], incident(needs_engineering=True))
    result = planner.reconcile(parent["id"])
    eng = next(s for s in data["spec"]["steps"] if s["role"] == "engineering")
    assert {"qa", "reviewer", "release"} <= set(data["team"])
    assert eng["id"] not in result["steps"]
    assert store.connection.execute("SELECT count(*) FROM approval_workflows").fetchone()[0] == 0
    store.close()


def test_missing_roles_blocks_without_dispatch_and_team_amendments_are_bounded(tmp_path):
    store = Store(tmp_path / "missing.db")
    parent = store.create_task("Respond", {}, ["incident response"])
    planner = Orchestrator(store, "pm", {"incident": "i"})
    result = planner.start_incident(parent["id"], incident())
    assert result["phase"] == "blocked"
    assert "research" in result["spec"]["blocked_reason"]
    assert planner.reconcile(parent["id"])["phase"] == "blocked"
    with pytest.raises(ValueError):
        planner.update_team(parent["id"], ["engineering"], "please fix")
    store.close()


def test_team_amendment_removes_only_undispatched_optional_steps(tmp_path):
    store = Store(tmp_path / "amend.db")
    parent = store.create_task("Respond", {}, ["incident response"])
    planner = Orchestrator(
        store, "pm", {"incident": "i", "research": "r", "security": "s", "documentation": "d"}
    )
    planner.start_incident(parent["id"], incident(category="security"))
    data = planner.reconcile(parent["id"])
    assert len(data["steps"]) == 3  # documentation waits for all three findings
    planner.update_team(parent["id"], ["research"], "narrow investigation")
    assert {s["role"] for s in planner.get(parent["id"])["spec"]["steps"]} >= {
        "incident",
        "documentation",
    }
    store.close()


def test_team_amendments_are_unique_bounded_monotonic_and_snapshot_targets(tmp_path):
    store = Store(tmp_path / "sequence.db")
    parent = store.create_task("Respond", {}, ["incident response"])
    targets = {"incident": "i", "research": "r", "security": "s"}
    planner = Orchestrator(store, "pm", targets)
    data = planner.start_incident(parent["id"], incident(category="security"))
    security_id = next(s["id"] for s in data["spec"]["steps"] if s["role"] == "security")
    planner.update_team(parent["id"], ["incident", "research"], "narrow")
    narrowed = planner.get(parent["id"])
    assert "security" not in narrowed["team"]
    assert "security" not in narrowed["selection_reasons"]
    amendment_planner = Orchestrator(
        store,
        "pm",
        {**targets, "community": "c"},
    )
    data = amendment_planner.update_team(
        parent["id"], ["incident", "research", "community"], "add community review"
    )
    community = next(s for s in data["spec"]["steps"] if s["role"] == "community")
    assert community["id"] != security_id
    assert data["targets"]["community"] == "c"
    restarted = Orchestrator(
        store, "pm", {"incident": "new-i", "research": "new-r", "community": "changed-c"}
    )
    with pytest.raises(ValueError, match="identity changed"):
        restarted.update_team(parent["id"], ["community"], "replace target")
    result = restarted.reconcile(parent["id"])
    child = store.get_task(result["steps"][community["id"]]["task_id"])
    assert store.get_handoff(result["steps"][community["id"]]["handoff_id"])["recipient"] == "c"
    assert child["status"] == "IN_PROGRESS"
    with pytest.raises(ValueError):
        planner.update_team(parent["id"], ["incident"] * 2, "duplicate")
    store.close()


def test_team_amendments_reject_terminal_plans_and_total_step_overflow(tmp_path):
    store = Store(tmp_path / "bound.db")
    parent = store.create_task("Respond", {}, ["incident response"])
    all_roles = {
        r: r
        for r in (
            "incident",
            "research",
            "security",
            "documentation",
            "knowledge",
            "community",
            "onboarding",
            "moderation",
            "scheduling",
            "analytics",
        )
    }
    planner = Orchestrator(store, "pm", all_roles)
    data = planner.start_incident(parent["id"], incident(category="security"))
    for n in range(7):
        data["spec"]["steps"].append(
            {"id": f"fixture-{n}", "role": "knowledge", "criteria": ["x"], "depends_on": []}
        )
    data["next_step_sequence"] = 20
    planner._save(parent["id"], data)
    with pytest.raises(ValueError, match="12 total steps"):
        planner.update_team(parent["id"], [*data["team"], "knowledge", "community"], "expand")
    parent = store.get_task(parent["id"])
    store.transition_task(parent["id"], "CANCELLED", parent["version"])
    with pytest.raises(ValueError, match="active incident"):
        planner.update_team(parent["id"], [], "closed")
    store.close()


def test_engineering_incident_blocks_when_pipeline_identity_missing(tmp_path):
    store = Store(tmp_path / "pipeline.db")
    parent = store.create_task("Respond", {}, ["incident response"])
    planner = Orchestrator(
        store,
        "pm",
        {"incident": "i", "research": "r", "engineering": "e"},
        engineering_route={"qa": "q", "release": "rel"},
    )
    data = planner.start_incident(parent["id"], incident(needs_engineering=True))
    assert data["phase"] == "blocked"
    assert "reviewer" in data["spec"]["blocked_reason"]
    assert planner.reconcile(parent["id"])["phase"] == "blocked"
    store.close()


def test_incident_plan_rejects_empty_and_shared_specialist_identities(tmp_path):
    from switchcrew.orchestration import Orchestrator
    from switchcrew.store import Store

    store = Store(tmp_path / "identities.db")
    incident = {
        "category": "bug",
        "severity": "SEV3",
        "summary": "Synthetic fixture",
        "needs_engineering": False,
        "evidence_refs": [],
    }
    for targets in (
        {"incident": "same", "research": "same"},
        {"incident": "incident", "research": ""},
    ):
        task = store.create_task("Investigate", {}, ["Evidence"])
        planner = Orchestrator(store, "pm", targets)
        data = planner.start_incident(task["id"], incident)
        assert data["phase"] == "blocked"
        assert (
            store.connection.execute(
                "SELECT count(*) FROM handoffs WHERE task_id=?", (task["id"],)
            ).fetchone()[0]
            == 0
        )
    store.close()
