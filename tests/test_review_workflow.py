import hashlib

import pytest

from switchcrew.approval import ApprovalWorkflow
from switchcrew.engineering import assignment_id
from switchcrew.store import Store


def setup(tmp_path, max_corrections=1):
    store = Store(tmp_path / "approval.db")
    workflow = ApprovalWorkflow(store, ["owner"])
    task = store.create_task("Make it work", {}, ["It works"])
    workflow.request(
        task["id"],
        "pm",
        "eng",
        "qa",
        "release",
        "job",
        "org/repo",
        "main",
        reviewer="reviewer",
        max_corrections=max_corrections,
    )
    data = workflow.decide(task["id"], "engineering", True, "owner", 1)
    eng_id = data["engineering_handoff"]
    result = {
        "status": "implemented",
        "assignment_id": assignment_id(eng_id),
        "base_revision": "b" * 40,
        "final_revision": "f" * 40,
        "diff": "patch",
        "checkout": "/tmp/checkout",
    }
    store.accept_handoff(eng_id, "eng")
    store.complete_handoff(eng_id, "eng", result, "engineering")
    data = workflow.reconcile(task["id"])
    return store, workflow, task["id"], data


def test_successful_qa_requires_independent_review(tmp_path):
    store, flow, task_id, data = setup(tmp_path)
    qa_id = data["qa_handoff"]
    store.accept_handoff(qa_id, "qa")
    store.complete_handoff(
        qa_id,
        "qa",
        {
            "status": "verified_success",
            "implementation_assignment": assignment_id(data["engineering_handoff"]),
            "tested_revision": "f" * 40,
            "commands": [{"exit_code": 0, "timed_out": False, "revision": "f" * 40}],
        },
    )
    data = flow.reconcile(task_id)
    assert data["phase"] == "review"
    data = flow.reconcile(task_id)
    assert (
        store.connection.execute(
            "SELECT COUNT(*) FROM handoffs WHERE recipient='reviewer'"
        ).fetchone()[0]
        == 1
    )
    assert store.get_task(task_id)["status"] == "QA_REVIEW"
    with pytest.raises(PermissionError):
        flow.evidence(task_id)
    h = store.get_handoff(data["review_handoff"])
    store.accept_handoff(h["id"], "reviewer")
    store.complete_handoff(
        h["id"],
        "reviewer",
        {
            "status": "review_accepted",
            "implementation_assignment": assignment_id(data["engineering_handoff"]),
            "reviewed_revision": "f" * 40,
            "base_revision": "b" * 40,
            "diff_sha256": hashlib.sha256(b"patch").hexdigest(),
            "findings": [],
            "commands": [{"exit_code": 0, "timed_out": False, "revision": "f" * 40}],
        },
    )
    data = flow.reconcile(task_id)
    assert data["phase"] == "human_review"
    assert flow.evidence(task_id)["status"] == "verified_success"
    store.close()


def test_request_requires_five_distinct_specialists(tmp_path):
    store = Store(tmp_path / "approval.db")
    flow = ApprovalWorkflow(store)
    task = store.create_task("objective", {}, [])
    with pytest.raises(ValueError):
        flow.request(task["id"], "pm", "eng", "qa", "release", "job", "repo", "main")
    store.close()


def complete_qa(store, data, result=None):
    qa = store.get_handoff(data["qa_handoff"])
    store.accept_handoff(qa["id"], "qa")
    store.complete_handoff(
        qa["id"],
        "qa",
        result
        or {
            "status": "verified_success",
            "implementation_assignment": assignment_id(data["engineering_handoff"]),
            "tested_revision": "f" * 40,
            "commands": [{"exit_code": 0, "timed_out": False, "revision": "f" * 40}],
        },
    )


def rejected_review(store, data, findings=None):
    h = store.get_handoff(data["review_handoff"])
    store.accept_handoff(h["id"], "reviewer")
    store.complete_handoff(
        h["id"],
        "reviewer",
        {
            "status": "review_rejected",
            "implementation_assignment": assignment_id(data["engineering_handoff"]),
            "reviewed_revision": data["revision"],
            "base_revision": "b" * 40,
            "diff_sha256": hashlib.sha256(b"patch").hexdigest(),
            "findings": findings if findings is not None else [{"message": "still broken"}],
            "command_findings": [],
            "commands": [{"exit_code": 1, "timed_out": False, "revision": data["revision"]}],
        },
    )


def test_reviewer_rejection_creates_bounded_correction_without_changing_scope(tmp_path):
    store, flow, task_id, data = setup(tmp_path, max_corrections=1)
    criteria = store.get_task(task_id)["acceptance_criteria"]
    complete_qa(store, data)
    data = flow.reconcile(task_id)
    rejected_review(store, data)
    data = flow.reconcile(task_id)
    assert data["phase"] == "engineering" and data["attempt"] == 1
    assert data["correction"]["implementation"]["final_revision"] == "f" * 40
    assert store.get_task(task_id)["acceptance_criteria"] == criteria
    correction = store.get_handoff(data["engineering_handoff"])
    assert correction["sender"] == "reviewer"
    assert correction["acceptance_criteria"] == criteria
    assert flow.reconcile(task_id)["engineering_handoff"] == correction["id"]
    store.close()


def test_correction_limit_and_repeated_identical_rejection_escalate(tmp_path):
    store, flow, task_id, data = setup(tmp_path, max_corrections=2)
    complete_qa(store, data)
    data = flow.reconcile(task_id)
    rejected_review(store, data)
    data = flow.reconcile(task_id)
    assert data["attempt"] == 1
    correction = store.get_handoff(data["engineering_handoff"])
    next_result = {
        "status": "implemented",
        "assignment_id": assignment_id(correction["id"]),
        "base_revision": "b" * 40,
        "final_revision": "e" * 40,
        "diff": "patch2",
        "checkout": "/tmp/checkout2",
    }
    store.accept_handoff(correction["id"], "eng")
    store.complete_handoff(correction["id"], "eng", next_result, "engineering")
    data = flow.reconcile(task_id)
    qa = store.get_handoff(data["qa_handoff"])
    store.accept_handoff(qa["id"], "qa")
    store.complete_handoff(
        qa["id"],
        "qa",
        {
            "status": "verified_success",
            "implementation_assignment": assignment_id(correction["id"]),
            "tested_revision": "e" * 40,
            "commands": [{"exit_code": 0, "timed_out": False, "revision": "e" * 40}],
        },
    )
    data = flow.reconcile(task_id)
    review = store.get_handoff(data["review_handoff"])
    store.accept_handoff(review["id"], "reviewer")
    store.complete_handoff(
        review["id"],
        "reviewer",
        {
            "status": "review_rejected",
            "implementation_assignment": assignment_id(correction["id"]),
            "reviewed_revision": "e" * 40,
            "base_revision": "b" * 40,
            "diff_sha256": hashlib.sha256(b"patch2").hexdigest(),
            "findings": [{"message": "still broken"}],
            "commands": [{"exit_code": 1, "timed_out": False, "revision": "e" * 40}],
        },
    )
    data = flow.reconcile(task_id)
    assert data["phase"] == "failed"
    assert data["escalation"]["reason"] == "repeated_identical_rejection"
    assert store.get_task(task_id)["status"] == "FAILED"
    store.close()


def test_zero_correction_budget_escalates_without_dispatch(tmp_path):
    store, flow, task_id, data = setup(tmp_path, max_corrections=0)
    complete_qa(store, data)
    data = flow.reconcile(task_id)
    rejected_review(store, data)
    data = flow.reconcile(task_id)
    assert data["phase"] == "failed" and data["attempt"] == 0
    assert data["escalation"]["reason"] == "correction_limit_exhausted"
    assert (
        store.connection.execute("SELECT COUNT(*) FROM handoffs WHERE recipient='eng'").fetchone()[
            0
        ]
        == 1
    )
    store.close()


def test_stale_and_malformed_review_fail_closed(tmp_path):
    store, flow, task_id, data = setup(tmp_path)
    complete_qa(store, data)
    data = flow.reconcile(task_id)
    h = store.get_handoff(data["review_handoff"])
    store.accept_handoff(h["id"], "reviewer")
    store.complete_handoff(
        h["id"],
        "reviewer",
        {
            "status": "review_accepted",
            "implementation_assignment": assignment_id(data["engineering_handoff"]),
            "reviewed_revision": "a" * 40,
            "base_revision": "b" * 40,
            "diff_sha256": hashlib.sha256(b"patch").hexdigest(),
            "findings": [],
            "commands": [{"exit_code": 0, "timed_out": False, "revision": "a" * 40}],
        },
    )
    data = flow.reconcile(task_id)
    assert data["phase"] == "failed"
    assert store.get_task(task_id)["status"] == "FAILED"
    store.close()


def test_malformed_review_result_fails_closed(tmp_path):
    store, flow, task_id, data = setup(tmp_path)
    complete_qa(store, data)
    data = flow.reconcile(task_id)
    h = store.get_handoff(data["review_handoff"])
    store.accept_handoff(h["id"], "reviewer")
    store.complete_handoff(
        h["id"],
        "reviewer",
        {
            "status": "review_accepted",
            "implementation_assignment": assignment_id(data["engineering_handoff"]),
            "reviewed_revision": "f" * 40,
            "base_revision": "b" * 40,
            "diff_sha256": hashlib.sha256(b"patch").hexdigest(),
            "findings": [],
            "commands": [None],
        },
    )
    assert flow.reconcile(task_id)["phase"] == "failed"
    store.close()


def test_legacy_missing_reviewer_and_cancelled_task_fail_closed(tmp_path):
    store, flow, task_id, data = setup(tmp_path)
    raw = flow.get(task_id)
    del raw["spec"]["reviewer"]
    flow._save(task_id, raw)
    data = flow.reconcile(task_id)
    assert data["phase"] == "failed"
    store.close()

    store, flow, task_id, data = setup(tmp_path / "cancel")
    task = store.get_task(task_id)
    store.transition_task(task_id, "CANCELLED", task["version"])
    before = store.connection.execute(
        "SELECT COUNT(*) FROM handoffs WHERE recipient='eng'"
    ).fetchone()[0]
    complete_qa(
        store,
        data,
        {
            "status": "qa_failed",
            "implementation_assignment": assignment_id(data["engineering_handoff"]),
            "tested_revision": "f" * 40,
            "commands": [{"exit_code": 1, "timed_out": False, "revision": "f" * 40}],
        },
    )
    assert flow.reconcile(task_id)["phase"] == "qa"
    after = store.connection.execute(
        "SELECT COUNT(*) FROM handoffs WHERE recipient='eng'"
    ).fetchone()[0]
    assert after == before
    store.close()
