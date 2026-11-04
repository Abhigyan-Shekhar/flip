import json

import pytest

from switchcrew.approval import ApprovalWorkflow
from switchcrew.engineering import assignment_id, git
from switchcrew.security_gate import (
    GateSecurityExecutor,
    configure_security_policy,
    require_security_gate,
    schedule_security_gate,
)
from switchcrew.store import Store


def _repo(path):
    path.mkdir(parents=True)
    git(path, "init")
    (path / "app.txt").write_text("safe\n")
    git(path, "add", ".")
    git(path, "-c", "user.name=Tester", "-c", "user.email=test@example.com", "commit", "-m", "base")
    return git(path, "rev-parse", "HEAD")


@pytest.fixture
def configured(tmp_path):
    repo = tmp_path / "authorized-roots" / "engineering-checkout-unique"
    revision = _repo(repo)
    store = Store(tmp_path / "state.db")
    workflow = ApprovalWorkflow(store, ["owner"])
    task = store.create_task(
        "Implement fix", {"room_id": "room", "project_id": "project-a"}, ["Works"]
    )
    workflow.request(
        task["id"], "pm", "eng", "qa", "release", "job", "org/repo", "main", reviewer="reviewer"
    )
    decision = workflow.decide(
        task["id"], "engineering", True, "owner", store.get_task(task["id"])["version"]
    )
    eng_id = decision["engineering_handoff"]
    store.accept_handoff(eng_id, "eng")
    result = {
        "status": "implemented",
        "assignment_id": assignment_id(eng_id),
        "final_revision": revision,
        "checkout": str(repo),
    }
    store.complete_handoff(eng_id, "eng", result, "engineering")
    data = workflow.reconcile(task["id"])
    yield store, workflow, task["id"], repo, revision, data
    store.close()


def _schedule(configured, security="security", threshold="high"):
    store, _workflow, task_id, repo, _revision, data = configured
    return schedule_security_gate(
        store,
        task_id,
        "pm",
        security,
        {"block_at_or_above": threshold, "allowed_repo_roots": [str(repo.parent)]},
    )


def _complete(configured, gate, report, sender="security"):
    store = configured[0]
    hid = gate["security_handoff_id"]
    store.accept_handoff(hid, sender)
    return store.complete_handoff(hid, sender, report, "security")


def test_gate_is_separate_assignment_bound_to_exact_engineering_evidence(configured):
    store, _workflow, parent_id, repo, revision, data = configured
    gate = _schedule(configured)
    child = store.get_task(gate["security_task_id"])
    assert child["id"] != parent_id
    assert child["source"]["source_evidence_task_id"] == parent_id
    assert "parent_task_id" not in child["source"]
    handoff = store.get_handoff(gate["security_handoff_id"])
    assert handoff["recipient"] == "security"
    assert child["source"]["revision"] == revision
    assert child["source"]["checkout"] == str(repo)
    with pytest.raises(PermissionError, match="pending"):
        require_security_gate(store, parent_id, data)


@pytest.mark.parametrize(
    "report",
    [
        {"status": "blocked_configuration", "revision": "a" * 40, "findings": [], "scanners": []},
        {
            "status": "scanner_failed",
            "revision": "a" * 40,
            "findings": [],
            "scanners": [{"status": "failed"}],
        },
        {
            "status": "verified_success",
            "revision": "b" * 40,
            "findings": [],
            "scanners": [{"status": "completed"}],
        },
        {"status": "verified_success", "revision": "a" * 40, "findings": [], "scanners": []},
    ],
)
def test_missing_scanner_outage_stale_or_forged_success_fail_closed(configured, report):
    store, _workflow, parent_id, _repo_path, _revision, data = configured
    gate = _schedule(configured)
    _complete(configured, gate, report)
    with pytest.raises(PermissionError):
        require_security_gate(store, parent_id, data)


@pytest.mark.parametrize(
    "severity,threshold,allowed",
    [
        ("Low", "high", True),
        ("Medium", "high", True),
        ("High", "high", False),
        ("High", "critical", True),
        ("Critical", "critical", False),
    ],
)
def test_policy_threshold_controls_release_evidence(configured, severity, threshold, allowed):
    store, _workflow, parent_id, _repo_path, revision, data = configured
    gate = _schedule(configured, threshold=threshold)
    report = {
        "status": "findings",
        "revision": revision,
        "findings": [{"severity": severity, "file": "app.txt"}],
        "scanners": [{"name": "configured", "status": "completed"}],
        "warnings": [],
    }
    _complete(configured, gate, report)
    if allowed:
        assert require_security_gate(store, parent_id, data) is True
    else:
        with pytest.raises(PermissionError, match="threshold"):
            require_security_gate(store, parent_id, data)


def test_no_gate_preserves_legacy_optional_policy(configured):
    store, _workflow, parent_id, _repo_path, _revision, data = configured
    assert require_security_gate(store, parent_id, data) is True


def test_persisted_project_policy_denies_missing_gate(configured):
    store, _workflow, parent_id, repo, _revision, data = configured
    policy = {"block_at_or_above": "high", "allowed_repo_roots": [str(repo.parent)]}
    configure_security_policy(store, "project-a", "pm", "security", policy)
    with pytest.raises(PermissionError, match="requires a security gate"):
        require_security_gate(store, parent_id, data)


def test_project_policy_survives_restart_and_cannot_be_weakened(configured, tmp_path):
    store, _workflow, _parent_id, repo, _revision, _data = configured
    policy = {"block_at_or_above": "high", "allowed_repo_roots": [str(repo.parent)]}
    configure_security_policy(store, "project-a", "pm", "security", policy)
    with pytest.raises(Exception, match="cannot be weakened"):
        configure_security_policy(
            store,
            "project-a",
            "pm",
            "security",
            {"block_at_or_above": "critical", "allowed_repo_roots": [str(repo.parent)]},
        )
    db_path = tmp_path / "state.db"
    store.close()
    reopened = Store(db_path)
    try:
        assert (
            reopened.connection.execute(
                "SELECT policy FROM project_security_policies WHERE project_id='project-a'"
            ).fetchone()
            is not None
        )
    finally:
        reopened.close()


def test_gate_can_be_scheduled_during_human_review(configured):
    store, workflow, parent_id, repo, _revision, data = configured
    data["phase"] = "review"
    workflow._save(parent_id, data)
    store.connection.execute("UPDATE tasks SET status='HUMAN_REVIEW' WHERE id=?", (parent_id,))
    gate = schedule_security_gate(
        store,
        parent_id,
        "pm",
        "security",
        {"block_at_or_above": "high", "allowed_repo_roots": [str(repo.parent)]},
    )
    assert gate["revision"] == data["revision"]


def test_gate_uses_configured_pm_identity(configured):
    store, _workflow, parent_id, repo, _revision, _data = configured
    with pytest.raises(PermissionError, match="configured PM"):
        schedule_security_gate(
            store,
            parent_id,
            "impostor",
            "security",
            {"block_at_or_above": "high", "allowed_repo_roots": [str(repo.parent)]},
        )


def test_gate_for_previous_revision_cannot_satisfy_current_assignment(configured):
    store, _workflow, parent_id, _repo_path, _revision, data = configured
    _schedule(configured)
    data["revision"] = "b" * 40
    with pytest.raises(PermissionError, match="cannot be verified"):
        require_security_gate(store, parent_id, data)


def test_symlinked_checkout_cannot_escape_authorized_root(configured, tmp_path):
    store, _workflow, parent_id, repo, _revision, data = configured
    escaped = tmp_path / "outside-checkout"
    _repo(escaped)
    alias = repo.parent / "checkout-alias"
    alias.symlink_to(escaped, target_is_directory=True)
    handoff_id = data["engineering_handoff"]
    result = store.get_handoff(handoff_id)["result"]
    result["checkout"] = str(alias)
    store.connection.execute(
        "UPDATE handoffs SET result=? WHERE id=?",
        (json.dumps(result), handoff_id),
    )
    with pytest.raises(PermissionError, match="outside"):
        schedule_security_gate(
            store,
            parent_id,
            "pm",
            "security",
            {"block_at_or_above": "high", "allowed_repo_roots": [str(repo.parent)]},
        )


@pytest.mark.asyncio
async def test_executor_uses_only_persisted_authorized_checkout_and_revision(configured):
    store, _workflow, _parent_id, repo, revision, _data = configured
    gate = _schedule(configured)

    class Runner:
        async def run(self, root, exact_revision):
            assert str(root) == str(repo)
            assert exact_revision == revision

            class Report:
                def to_dict(self):
                    return {
                        "status": "verified_success",
                        "revision": revision,
                        "findings": [],
                        "scanners": [{"status": "completed"}],
                        "warnings": [],
                    }

            return Report()

    executor = GateSecurityExecutor(store, "security", Runner())
    handoff = store.get_handoff(gate["security_handoff_id"])
    state = {"task_id": handoff["task_id"], "handoff_id": handoff["id"]}
    report = await executor(state)
    assert report["status"] == "verified_success"
    denied = await executor({"task_id": "other", "handoff_id": handoff["id"]})
    assert denied["status"] == "blocked"


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["cancel", "revision"])
async def test_executor_discards_success_if_parent_changes_during_scan(configured, change):
    store, workflow, parent_id, repo, revision, data = configured
    gate = _schedule(configured)

    class Runner:
        async def run(self, _root, _revision):
            if change == "cancel":
                task = store.get_task(parent_id)
                store.transition_task(parent_id, "CANCELLED", task["version"])
            else:
                latest = workflow.get(parent_id)
                latest["revision"] = "b" * 40
                workflow._save(parent_id, latest)

            class Report:
                def to_dict(self):
                    return {
                        "status": "verified_success",
                        "revision": revision,
                        "findings": [],
                        "scanners": [{"status": "completed"}],
                        "warnings": [],
                    }

            return Report()

    executor = GateSecurityExecutor(store, "security", Runner())
    handoff = store.get_handoff(gate["security_handoff_id"])
    result = await executor({"task_id": handoff["task_id"], "handoff_id": handoff["id"]})
    assert result["status"] == "blocked"
    assert result["reason"] in {
        "security_gate_parent_inactive",
        "security_gate_engineering_unavailable",
    }
