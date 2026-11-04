"""Reproducible two-task local lesson reuse experiment.

Uses a temporary real Git checkout, real subprocess regression runs, SQLite
workflow evidence fixtures, and a controlled Memory transport. It makes no
live Waggle or Switch calls and never touches the working repository.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .approval import ApprovalWorkflow
from .engineering import assignment_id, qa_handoff_id
from .lessons import LessonService
from .memory import MemoryResult
from .store import Store, encode

PROJECT = "github.com/switchcrew/local-learning-acceptance"
REGRESSION = [sys.executable, "test_normalize.py"]


class ControlledMemory:
    """In-process controlled transport; not evidence of a live Waggle write."""

    def __init__(self) -> None:
        self.project = PROJECT
        self.rows: dict[str, str] = {}
        self.observe_calls = 0
        self.recall_calls = 0

    async def observe(self, user_message: str, assistant_response: str, role: str, session_id: str):
        self.observe_calls += 1
        self.rows[session_id] = assistant_response
        return MemoryResult(persisted=True)

    async def recall(self, query: str, role: str, session_id: str):
        self.recall_calls += 1
        return MemoryResult(context="\n".join(self.rows.values()))


def _git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


def _run(root: Path, argv: list[str]) -> dict[str, Any]:
    result = subprocess.run(argv, cwd=root, capture_output=True, text=True, timeout=20, check=False)
    return {
        "command": argv,
        "exit_code": result.returncode,
        "timed_out": False,
        "revision": _git(root, "rev-parse", "HEAD"),
        "output_sha256": hashlib.sha256((result.stdout + result.stderr).encode()).hexdigest(),
    }


def _commit(root: Path, message: str) -> str:
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(root), "commit", "-m", message], check=True, capture_output=True
    )
    return _git(root, "rev-parse", "HEAD")


def _store_handoff(
    store: Store, handoff_id: str, task_id: str, sender: str, recipient: str, result: dict[str, Any]
) -> None:
    store.connection.execute(
        "INSERT INTO handoffs(id,task_id,sender,recipient,question,acceptance_criteria,"
        "status,result,version) "
        "VALUES(?,?,?,?,?,?, 'completed', ?, 1)",
        (
            handoff_id,
            task_id,
            sender,
            recipient,
            "Normalize imported user keys",
            encode(["Surrounding whitespace does not create a different user key"]),
            encode(result),
        ),
    )


def _seed_verified_retry(
    store: Store,
    repo: Path,
    task_id: str,
    base_revision: str,
    final_revision: str,
    fail_record: dict[str, Any],
    pass_record: dict[str, Any],
    review_record: dict[str, Any],
) -> None:
    ApprovalWorkflow(store)
    task = store.create_task(
        "Fix imported key normalization",
        {"project_id": PROJECT},
        ["Surrounding whitespace does not create a different user key"],
        task_id=task_id,
    )
    for status in ("PLANNED", "IN_PROGRESS", "QA_REVIEW", "PASSED", "HUMAN_REVIEW"):
        task = store.transition_task(task_id, status, task["version"])
    failed_eng = f"eng-before-correction-{task_id}"
    failed_rev = fail_record["revision"]
    failed_qa = qa_handoff_id(failed_eng, failed_rev)
    _store_handoff(
        store,
        failed_eng,
        task_id,
        "pm",
        "engineering",
        {"status": "implemented", "final_revision": failed_rev},
    )
    _store_handoff(
        store,
        failed_qa,
        task_id,
        "engineering",
        "qa",
        {
            "status": "qa_failed",
            "implementation_assignment": assignment_id(failed_eng),
            "tested_revision": failed_rev,
            "commands": [fail_record],
        },
    )

    eng_id = f"eng-after-correction-{task_id}"
    diff = _git(repo, "diff", base_revision, final_revision)
    eng_result = {
        "status": "implemented",
        "assignment_id": assignment_id(eng_id),
        "base_revision": base_revision,
        "final_revision": final_revision,
        "diff": diff,
    }
    _store_handoff(store, eng_id, task_id, "pm", "engineering", eng_result)
    qa_id = qa_handoff_id(eng_id, final_revision)
    qa = {
        "status": "verified_success",
        "implementation_assignment": assignment_id(eng_id),
        "tested_revision": final_revision,
        "commands": [pass_record],
    }
    _store_handoff(store, qa_id, task_id, "engineering", "qa", qa)
    reviewer_id = "review-" + hashlib.sha256(f"{eng_id}:{final_revision}".encode()).hexdigest()
    reviewer = {
        "status": "review_accepted",
        "implementation_assignment": assignment_id(eng_id),
        "reviewed_revision": final_revision,
        "base_revision": base_revision,
        "diff_sha256": hashlib.sha256(diff.encode()).hexdigest(),
        "findings": [],
        "command_findings": [],
        "commands": [review_record],
    }
    _store_handoff(store, reviewer_id, task_id, "qa", "reviewer", reviewer)
    workflow = {
        "spec": {"engineering": "engineering", "qa": "qa", "reviewer": "reviewer"},
        "phase": "human_review",
        "attempt": 1,
        "retry_history": [
            {
                "attempt": 1,
                "rejecting_agent": "qa",
                "rejected_handoff": failed_qa,
                "revision": failed_rev,
                "implementation_assignment": assignment_id(failed_eng),
                "findings": [
                    {
                        "type": "qa_command_failed",
                        "command": json.dumps(fail_record.get("command")),
                        "exit_code": fail_record.get("exit_code"),
                        "timed_out": fail_record.get("timed_out"),
                        "output_summary": "",
                        "output_sha256": fail_record.get("output_sha256", ""),
                    }
                ],
            }
        ],
        "revision": final_revision,
        "engineering_handoff": eng_id,
        "qa_handoff": qa_id,
        "review_handoff": reviewer_id,
    }
    store.connection.execute(
        "INSERT INTO approval_workflows(task_id,data) VALUES(?,?)", (task_id, encode(workflow))
    )


async def run_two_task_learning() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="switchcrew-learning-") as temporary:
        root = Path(temporary) / "repo"
        root.mkdir()
        subprocess.run(["git", "init", str(root)], check=True, capture_output=True)
        subprocess.run(
            ["git", "-C", str(root), "config", "user.email", "acceptance@example.test"], check=True
        )
        subprocess.run(
            ["git", "-C", str(root), "config", "user.name", "SwitchCrew acceptance"], check=True
        )
        (root / "normalize.py").write_text(
            "def normalize(value):\n    return value.casefold()\n", encoding="utf-8"
        )
        (root / "test_normalize.py").write_text(
            "from normalize import normalize\nassert normalize(' Alice ') == 'alice'\n",
            encoding="utf-8",
        )
        failed_revision = _commit(root, "task one buggy normalization")
        failed = _run(root, REGRESSION)
        if failed["exit_code"] == 0:
            raise RuntimeError("Task one fixture did not reproduce the regression")

        # The observed fix is only turned into a candidate after the correction,
        # exact-revision QA, and exact-revision Reviewer receipts are assembled.
        (root / "normalize.py").write_text(
            "def normalize(value):\n    return value.strip().casefold()\n", encoding="utf-8"
        )
        final_revision = _commit(root, "task one correction")
        passed = _run(root, REGRESSION)
        if passed["exit_code"] != 0:
            raise RuntimeError("Task one correction failed its regression")
        review_record = _run(root, ["git", "diff", "--check"])
        if review_record["exit_code"] != 0:
            raise RuntimeError("Task one review fixture failed")

        store = Store(Path(temporary) / "workflow.sqlite3")
        _seed_verified_retry(
            store, root, "task-one", failed_revision, final_revision, failed, passed, review_record
        )
        actors = {
            "owner": {"projects": [PROJECT]},
            "approver": {"projects": [PROJECT], "approve_projects": [PROJECT]},
            "consumer": {"projects": [PROJECT]},
        }
        memory = ControlledMemory()
        service = LessonService(store, trusted_actors=actors, memory=memory)
        lesson = service.propose_from_task(
            "task-one",
            actor_id="owner",
            project_id=PROJECT,
            scope="project",
            summary="Strip surrounding whitespace before casefolding imported keys",
            failure_signature="case-insensitive key mismatch after surrounding whitespace",
            root_cause="The initial helper casefolded but did not strip the input.",
            root_cause_status="verified",
            correction="Replace value.casefold() with value.strip().casefold()",
            regression_command=REGRESSION,
            applicability=["Case-insensitive string identifiers from user or document input"],
        )
        approved = await service.approve_lesson(lesson["id"], actor_id="approver")
        if approved["approval_status"] != "approved" or approved["waggle_write_status"] != "stored":
            raise RuntimeError("Controlled memory did not confirm approved lesson storage")

        # Task two begins with the same bug. First demonstrate the regression,
        # then retrieve through the controlled memory transport and apply its
        # locally verified canonical lesson before rerunning the same test.
        (root / "normalize.py").write_text(
            "def normalize(value):\n    return value.casefold()\n", encoding="utf-8"
        )
        task_two_base = _commit(root, "task two starts with similar buggy normalization")
        task_two_command = list(REGRESSION)
        task_two_before = _run(root, task_two_command)
        if task_two_before["exit_code"] == 0:
            raise RuntimeError("Task two fixture did not reproduce the similar regression")
        store.create_task(
            "Fix imported key normalization",
            {"project_id": PROJECT},
            ["Surrounding whitespace does not create a different user key"],
            task_id="task-two",
        )
        retrieved = await service.retrieve_lessons(
            "normalize imported user key whitespace casefold",
            actor_id="consumer",
            project_id=PROJECT,
            task_id="task-two",
        )
        if not retrieved["lessons"] or retrieved["lessons"][0]["id"] != lesson["id"]:
            raise RuntimeError("Second task did not retrieve the approved lesson")
        correction = retrieved["lessons"][0]["correction"]
        match = re.search(r"value\.strip\(\)\.casefold\(\)", correction)
        if not match:
            raise RuntimeError("Retrieved lesson did not contain the verified correction")
        (root / "normalize.py").write_text(
            f"def normalize(value):\n    return {match.group(0)}\n", encoding="utf-8"
        )
        _commit(root, "task two applies retrieved regression lesson")
        task_two_after = _run(root, task_two_command)
        if task_two_after["exit_code"] != 0:
            raise RuntimeError("Task two did not pass after applying the retrieved lesson")
        task_two_final = task_two_after["revision"]
        task_two_review = _run(root, ["git", "diff", "--check"])
        _seed_verified_retry(
            store,
            root,
            "task-two",
            task_two_base,
            task_two_final,
            task_two_before,
            task_two_after,
            task_two_review,
        )
        use = service.record_use("task-two", lesson["id"], actor_id="consumer")

        result = {
            "status": "PASS",
            "mode": "controlled_local_acceptance",
            "observed_at": datetime.now(UTC).isoformat(),
            "real_git_subprocesses": True,
            "temporary_repository_only": True,
            "live_waggle_called": False,
            "controlled_memory_observe_calls": memory.observe_calls,
            "controlled_memory_recall_calls": memory.recall_calls,
            "task_one": {
                "initial_revision": failed_revision,
                "qa_failed_exit_code": failed["exit_code"],
                "corrected_revision": final_revision,
                "regression_exit_code": passed["exit_code"],
                "qa_and_reviewer_validated": True,
            },
            "lesson": {
                "id": lesson["id"],
                "approval_status": approved["approval_status"],
                "root_cause_status": approved["root_cause_status"],
                "waggle_write_status": approved["waggle_write_status"],
            },
            "task_two": {
                "baseline_exit_code": task_two_before["exit_code"],
                "reused_lesson_id": retrieved["lessons"][0]["id"],
                "regression_exit_code_after_reuse": task_two_after["exit_code"],
                "behavior_changed_after_memory_retrieval": task_two_before["exit_code"]
                != task_two_after["exit_code"],
                "durable_use_receipt": use["outcome"],
            },
            "limitations": [
                "Waggle transport is controlled and in-process; this is not live integration "
                "evidence.",
                "QA and Reviewer receipts are local fixtures bound to real Git revisions and "
                "subprocess results.",
                "The test repository and SQLite workflow database are deleted at process exit.",
            ],
        }
        store.close()
        return result


async def main(report_path: str | None = None) -> int:
    try:
        result = await run_two_task_learning()
    except Exception:
        result = {
            "status": "FAIL",
            "observed_at": datetime.now(UTC).isoformat(),
            "reason": "controlled_learning_acceptance_failed",
        }
    encoded = json.dumps(result, indent=2)
    if report_path:
        descriptor = os.open(report_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as target:
            target.write(encoded + "\n")
    print(encoded)
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args.report)))
