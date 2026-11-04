import hashlib
import json

import pytest

from switchcrew.approval import ApprovalWorkflow
from switchcrew.engineering import assignment_id, qa_handoff_id
from switchcrew.lessons import LessonService
from switchcrew.memory import MemoryResult
from switchcrew.store import Store, encode

PROJECT = "github.com/example/project"
BASE = "a" * 40
FAILED_REVISION = "b" * 40
FINAL_REVISION = "c" * 40
REGRESSION = ["python", "tests/test_normalize.py"]


def actor_map():
    return {
        "alice": {"projects": [PROJECT], "teams": ["team-a"], "orgs": ["org-a"]},
        "bob": {"projects": [PROJECT], "teams": ["team-a"], "orgs": ["org-a"]},
        "approver": {
            "projects": [PROJECT],
            "teams": ["team-a"],
            "orgs": ["org-a"],
            "approve_personal": ["alice"],
            "approve_teams": ["team-a"],
            "approve_orgs": ["org-a"],
            "approve_projects": [PROJECT],
        },
    }


class ControlledMemory:
    def __init__(self, *, persisted=True):
        self.project = PROJECT
        self.persisted = persisted
        self.records = {}
        self.observe_calls = 0
        self.recall_calls = 0

    async def observe(self, user_message, assistant_response, role, session_id):
        self.observe_calls += 1
        self.records[session_id] = (user_message, assistant_response)
        return MemoryResult(
            persisted=self.persisted, warning=None if self.persisted else "not confirmed"
        )

    async def recall(self, query, role, session_id):
        self.recall_calls += 1
        text = "\n".join(response for _, response in self.records.values())
        return MemoryResult(context=text)


def insert_handoff(store, *, handoff_id, task_id, sender, recipient, result):
    store.connection.execute(
        "INSERT INTO handoffs(id,task_id,sender,recipient,question,acceptance_criteria,"
        "status,result,version) "
        "VALUES(?,?,?,?,?,?, 'completed', ?, 1)",
        (
            handoff_id,
            task_id,
            sender,
            recipient,
            "Fix the key normalization regression",
            encode(["Preserve stable normalized keys"]),
            encode(result),
        ),
    )


def verified_retry_task(store, task_id="task-one", *, final_revision=FINAL_REVISION):
    ApprovalWorkflow(store)
    task = store.create_task(
        "Fix key normalization",
        {"project_id": PROJECT},
        ["Preserve stable normalized keys"],
        task_id=task_id,
    )
    for status in ("PLANNED", "IN_PROGRESS", "QA_REVIEW", "PASSED", "HUMAN_REVIEW"):
        task = store.transition_task(task_id, status, task["version"])
    failed_engineering = "eng-initial-" + task_id
    failed_qa = qa_handoff_id(failed_engineering, FAILED_REVISION)
    insert_handoff(
        store,
        handoff_id=failed_engineering,
        task_id=task_id,
        sender="pm",
        recipient="engineering",
        result={"status": "implemented", "final_revision": FAILED_REVISION},
    )
    insert_handoff(
        store,
        handoff_id=failed_qa,
        task_id=task_id,
        sender="engineering",
        recipient="qa",
        result={
            "status": "qa_failed",
            "implementation_assignment": assignment_id(failed_engineering),
            "tested_revision": FAILED_REVISION,
            "commands": [
                {
                    "command": REGRESSION,
                    "exit_code": 1,
                    "timed_out": False,
                    "revision": FAILED_REVISION,
                }
            ],
        },
    )
    final_engineering = "eng-corrected-" + task_id
    diff = "diff --git a/normalize.py b/normalize.py\n+strip before casefold\n"
    engineering_result = {
        "status": "implemented",
        "assignment_id": assignment_id(final_engineering),
        "base_revision": BASE,
        "final_revision": final_revision,
        "diff": diff,
    }
    insert_handoff(
        store,
        handoff_id=final_engineering,
        task_id=task_id,
        sender="pm",
        recipient="engineering",
        result=engineering_result,
    )
    qa_id = qa_handoff_id(final_engineering, final_revision)
    qa_result = {
        "status": "verified_success",
        "implementation_assignment": assignment_id(final_engineering),
        "tested_revision": final_revision,
        "commands": [
            {
                "command": REGRESSION,
                "exit_code": 0,
                "timed_out": False,
                "revision": final_revision,
                "output_sha256": "d" * 64,
            }
        ],
    }
    insert_handoff(
        store,
        handoff_id=qa_id,
        task_id=task_id,
        sender="engineering",
        recipient="qa",
        result=qa_result,
    )
    reviewer_id = (
        "review-" + hashlib.sha256(f"{final_engineering}:{final_revision}".encode()).hexdigest()
    )
    reviewer_result = {
        "status": "review_accepted",
        "implementation_assignment": assignment_id(final_engineering),
        "reviewed_revision": final_revision,
        "base_revision": BASE,
        "diff_sha256": hashlib.sha256(diff.encode()).hexdigest(),
        "findings": [],
        "command_findings": [],
        "commands": [
            {
                "command": ["git", "diff", "--check"],
                "exit_code": 0,
                "timed_out": False,
                "revision": final_revision,
            }
        ],
    }
    insert_handoff(
        store,
        handoff_id=reviewer_id,
        task_id=task_id,
        sender="qa",
        recipient="reviewer",
        result=reviewer_result,
    )
    workflow = {
        "spec": {"engineering": "engineering", "qa": "qa", "reviewer": "reviewer"},
        "phase": "human_review",
        "attempt": 1,
        "retry_history": [
            {
                "attempt": 1,
                "rejecting_agent": "qa",
                "rejected_handoff": failed_qa,
                "revision": FAILED_REVISION,
                "implementation_assignment": assignment_id(failed_engineering),
                "findings": [
                    {
                        "type": "qa_command_failed",
                        "command": json.dumps(REGRESSION),
                        "exit_code": 1,
                        "timed_out": False,
                        "output_summary": "",
                        "output_sha256": "",
                    }
                ],
            }
        ],
        "revision": final_revision,
        "engineering_handoff": final_engineering,
        "qa_handoff": qa_id,
        "review_handoff": reviewer_id,
    }
    store.connection.execute(
        "INSERT INTO approval_workflows(task_id,data) VALUES(?,?)", (task_id, encode(workflow))
    )
    return task_id, workflow


def propose(
    service,
    task_id,
    *,
    scope="project",
    scope_id=None,
    root_cause_status="verified",
    root_cause="The normalizer lowercased before removing surrounding whitespace.",
    supersedes=None,
    contradicts=None,
):
    return service.propose_from_task(
        task_id,
        actor_id="alice",
        project_id=PROJECT,
        scope=scope,
        scope_id=scope_id,
        summary="Normalize keys before comparing them",
        failure_signature="key normalization whitespace regression",
        root_cause=root_cause,
        root_cause_status=root_cause_status,
        correction="Replace value.casefold() with value.strip().casefold()",
        regression_command=REGRESSION,
        applicability=["Python string keys that arrive from imported notes"],
        supersedes=supersedes,
        contradicts=contradicts,
    )


def test_requires_exact_revision_bound_retry_qa_review_and_regression_command(tmp_path):
    store = Store(tmp_path / "state.sqlite3")
    verified_retry_task(store)
    service = LessonService(store, trusted_actors=actor_map())
    item = propose(service, "task-one")
    assert item["approval_status"] == "candidate"
    assert item["evidence"]["final_revision"] == FINAL_REVISION
    assert item["evidence"]["qa_status"] == "verified_success"
    assert item["evidence"]["reviewer_status"] == "review_accepted"
    with pytest.raises(PermissionError, match="regression command"):
        service.propose_from_task(
            "task-one",
            actor_id="alice",
            project_id=PROJECT,
            scope="project",
            summary="Other",
            failure_signature="other",
            root_cause="known",
            root_cause_status="verified",
            correction="Correct",
            regression_command=["python", "missing.py"],
            applicability=["Python"],
        )
    store.close()


async def test_hypotheses_stale_evidence_and_acl_are_not_verified_memory(tmp_path):
    store = Store(tmp_path / "state.sqlite3")
    verified_retry_task(store)
    memory = ControlledMemory()
    service = LessonService(store, trusted_actors=actor_map(), memory=memory)
    hypothesis = propose(
        service,
        "task-one",
        root_cause_status="hypothesis",
        root_cause="Could be a locale-specific ordering issue",
    )
    with pytest.raises(PermissionError, match="hypothesis"):
        await service.approve_lesson(hypothesis["id"], actor_id="approver")
    assert not memory.observe_calls

    verified_retry_task(store, "task-two", final_revision="e" * 40)
    candidate = propose(service, "task-two")
    with pytest.raises(PermissionError, match="cannot approve"):
        await service.approve_lesson(candidate["id"], actor_id="bob")
    store.connection.execute(
        "UPDATE approval_workflows SET data=replace(data, ?, ?) WHERE task_id=?",
        ("e" * 40, "f" * 40, "task-two"),
    )
    with pytest.raises(PermissionError, match="stale"):
        await service.approve_lesson(candidate["id"], actor_id="approver")
    assert not memory.observe_calls
    store.close()


async def test_project_lesson_requires_explicit_approval_and_never_blindly_retries_waggle(tmp_path):
    store = Store(tmp_path / "state.sqlite3")
    verified_retry_task(store)
    memory = ControlledMemory(persisted=False)
    service = LessonService(store, trusted_actors=actor_map(), memory=memory)
    store.create_task(
        "Consume verified lesson",
        {"project_id": PROJECT},
        ["Verify the reused regression"],
        task_id="task-two",
    )
    item = propose(service, "task-one")
    assert (
        await service.retrieve_lessons(
            "normalization whitespace", actor_id="alice", project_id=PROJECT
        )
    )["lessons"] == []
    with pytest.raises(PermissionError):
        await service.approve_lesson(item["id"], actor_id="alice")
    approved = await service.approve_lesson(item["id"], actor_id="approver")
    assert approved["approval_status"] == "approved"
    assert approved["waggle_write_status"] == "unknown"
    assert memory.observe_calls == 1
    await service.approve_lesson(item["id"], actor_id="approver")
    assert memory.observe_calls == 1
    recalled = await service.retrieve_lessons(
        "normalization whitespace", actor_id="bob", project_id=PROJECT, task_id="task-two"
    )
    assert recalled["lessons"][0]["id"] == item["id"]
    assert recalled["trust"].startswith("untrusted")
    assert memory.recall_calls == 2
    store.close()


async def test_supersession_and_contradiction_are_explicit_local_relations(tmp_path):
    store = Store(tmp_path / "state.sqlite3")
    verified_retry_task(store, "task-old")
    old_service = LessonService(store, trusted_actors=actor_map())
    old = propose(old_service, "task-old")
    await old_service.approve_lesson(old["id"], actor_id="approver")

    # A second verified correction can explicitly supersede a prior lesson.
    verified_retry_task(store, "task-new", final_revision="e" * 40)
    new = propose(old_service, "task-new", supersedes=[old["id"]])
    await old_service.approve_lesson(new["id"], actor_id="approver")
    result = await old_service.retrieve_lessons(
        "key normalization", actor_id="alice", project_id=PROJECT
    )
    assert result["lessons"][0]["id"] == new["id"]
    relations = old_service._relation_summary(new["id"])
    assert any(r["relation"] == "supersedes" and r["target_id"] == old["id"] for r in relations)

    verified_retry_task(store, "task-conflict", final_revision="f" * 40)
    conflict = propose(old_service, "task-conflict", contradicts=[new["id"]])
    await old_service.approve_lesson(conflict["id"], actor_id="approver")
    conflict_relations = old_service._relation_summary(conflict["id"])
    assert any(
        item["relation"] == "contradicts"
        and item["source_id"] == conflict["id"]
        and item["target_id"] == new["id"]
        for item in conflict_relations
    )
    assert any(
        item["relation"] == "contradicts"
        and item["source_id"] == new["id"]
        and item["target_id"] == conflict["id"]
        for item in conflict_relations
    )
    store.close()


async def test_personal_and_team_lessons_never_project_to_shared_waggle(tmp_path):
    store = Store(tmp_path / "state.sqlite3")
    verified_retry_task(store)
    memory = ControlledMemory()
    service = LessonService(store, trusted_actors=actor_map(), memory=memory)
    personal = propose(service, "task-one", scope="personal")
    approved = await service.approve_lesson(personal["id"], actor_id="alice")
    assert memory.observe_calls == 0
    assert approved["waggle_write_status"] == "local_only"
    assert await service.retrieve_lessons(
        "key normalization", actor_id="bob", project_id=PROJECT
    ) == {
        "status": "no_verified_lessons",
        "trust": "untrusted_guidance_verify_against_current_repository_and_task",
        "project_id": PROJECT,
        "lessons": [],
        "warnings": [],
    }
    store.close()


def test_lesson_project_must_match_persisted_task_project(tmp_path):
    store = Store(tmp_path / "state.sqlite3")
    verified_retry_task(store)
    actors = actor_map()
    actors["alice"]["projects"].append("github.com/example/other")
    service = LessonService(store, trusted_actors=actors)

    with pytest.raises(PermissionError, match="task's persisted project"):
        service.propose_from_task(
            "task-one",
            actor_id="alice",
            project_id="github.com/example/other",
            scope="project",
            summary="Normalize keys",
            failure_signature="mismatch",
            root_cause="unknown",
            root_cause_status="unknown",
            correction="Strip before casefold",
            regression_command=REGRESSION,
            applicability=["Python strings"],
        )
    store.close()
