"""Durable approvals and revision-bound release policy shared by PM and workers."""

import json
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, uuid5

from .engineering import assignment_id, qa_handoff_id
from .store import ConflictError, encode


class ApprovalWorkflow:
    def __init__(self, store, approvers=()):
        self.store = store
        self.approvers = set(approvers)
        with store._lock:
            store.connection.execute(
                "CREATE TABLE IF NOT EXISTS approval_workflows "
                "(task_id TEXT PRIMARY KEY REFERENCES tasks(id), data TEXT NOT NULL)"
            )

    def get(self, task_id):
        with self.store._lock:
            row = self.store.connection.execute(
                "SELECT data FROM approval_workflows WHERE task_id=?", (task_id,)
            ).fetchone()
            return json.loads(row[0]) if row else None

    def _save(self, task_id, data):
        self.store.connection.execute(
            "INSERT INTO approval_workflows VALUES(?,?) "
            "ON CONFLICT(task_id) DO UPDATE SET data=excluded.data",
            (task_id, encode(data)),
        )

    def request(self, task_id, pm, engineering, qa, release, job_key, repository, base):
        if len({pm, engineering, qa, release}) != 4 or not all(
            (pm, engineering, qa, release, job_key, repository, base)
        ):
            raise ValueError("Four distinct specialist identities and an operator job are required")
        with self.store._lock, self.store.transaction():
            spec = dict(
                pm=pm,
                engineering=engineering,
                qa=qa,
                release=release,
                job_key=job_key,
                repository=repository,
                base=base,
            )
            old = self.get(task_id)
            if old:
                if old["spec"] != spec:
                    raise ConflictError("Engineering request changed")
                return old
            task = self.store.get_task(task_id)
            if task["status"] != "DRAFT":
                raise ConflictError("Engineering request must start as draft")
            self.store.transition_task(task_id, "AWAITING_APPROVAL", task["version"])
            data = {"spec": spec, "phase": "awaiting_engineering_approval"}
            self._save(task_id, data)
            return data

    def decide(self, task_id, stage, approved, actor, expected_version):
        """Actor is supplied by authenticated transport, never by request JSON."""
        if type(approved) is not bool or type(expected_version) is not int:
            raise ValueError("An explicit boolean decision and integer version are required")
        if actor not in self.approvers:
            raise PermissionError("Actor is not an authorized human approver")
        if stage not in {"engineering", "release"}:
            raise ValueError("Unknown approval stage")
        with self.store._lock, self.store.transaction():
            task = self.store._check(task_id, expected_version)
            if not self.parent_active(task):
                raise PermissionError("Parent orchestration is no longer active")
            data = self.get(task_id)
            expected = "awaiting_engineering_approval" if stage == "engineering" else "human_review"
            if not data or data["phase"] != expected:
                raise ConflictError("Approval is not pending")
            if stage == "release":
                self.evidence(task_id)
            required_status = "AWAITING_APPROVAL" if stage == "engineering" else "HUMAN_REVIEW"
            if task["status"] != required_status:
                raise ConflictError("Task is no longer awaiting approval")
            decision = {
                "actor": actor,
                "approved": approved,
                "task_version": expected_version,
                "decided_at": datetime.now(UTC).isoformat(),
            }
            if stage == "release":
                decision.update(
                    revision=data["revision"],
                    repository=data["spec"]["repository"],
                    base=data["spec"]["base"],
                )
            data[stage + "_approval"] = decision
            self.store._event(task_id, stage + ".decision", decision, expected_version)
            if not approved:
                data["phase"] = "rejected"
                self.store.transition_task(task_id, "CANCELLED", expected_version)
            elif stage == "engineering":
                task = self.store.transition_task(task_id, "PLANNED", expected_version)
                task = self.store.transition_task(task_id, "IN_PROGRESS", task["version"])
                handoff_id = str(uuid5(NAMESPACE_URL, f"{task_id}:engineering"))
                self.store.create_handoff(
                    task_id,
                    data["spec"]["pm"],
                    data["spec"]["engineering"],
                    task["objective"],
                    task["acceptance_criteria"],
                    task["version"],
                    handoff_id=handoff_id,
                )
                data.update(phase="engineering", engineering_handoff=handoff_id)
            else:
                task = self.store.transition_task(task_id, "PUBLISH_APPROVED", expected_version)
                handoff_id = str(uuid5(NAMESPACE_URL, f"{task_id}:release:{data['revision']}"))
                self.store.create_handoff(
                    task_id,
                    data["spec"]["pm"],
                    data["spec"]["release"],
                    task["objective"],
                    task["acceptance_criteria"],
                    task["version"],
                    handoff_id=handoff_id,
                )
                data.update(phase="release", release_handoff=handoff_id)
            self._save(task_id, data)
            return data

    def parent_active(self, task):
        parent_id = task["source"].get("parent_task_id")
        parent = self.store.get_task(parent_id) if parent_id else None
        return not parent_id or bool(parent and parent["status"] == "IN_PROGRESS")

    def require_engineering(self, state, agent):
        data = self.get(state["task_id"])
        task = self.store.get_task(state["task_id"])
        handoff = self.store.get_handoff(state["handoff_id"])
        if not (
            data
            and data["phase"] == "engineering"
            and data.get("engineering_approval", {}).get("approved") is True
            and data["spec"]["engineering"] == agent
            and data.get("engineering_handoff") == state["handoff_id"]
            and task["status"] == "IN_PROGRESS"
            and self.parent_active(task)
            and handoff
            and handoff["recipient"] == agent
            and handoff["sender"] == data["spec"]["pm"]
            and handoff["task_id"] == state["task_id"]
            and handoff["question"] == state["question"]
            and handoff["acceptance_criteria"] == state["acceptance_criteria"]
        ):
            raise PermissionError("Human engineering approval required")
        return data

    def reconcile(self, task_id):
        """Read only trusted persisted worker results; never accept client QA claims."""
        from .engineering import enqueue_independent_qa

        with self.store._lock, self.store.transaction():
            data = self.get(task_id)
            task = self.store.get_task(task_id)
            if not data or task["status"] in {"CANCELLED", "COMPLETED", "BLOCKED", "FAILED"}:
                return data
            if data["phase"] == "engineering":
                h = self.store.get_handoff(data["engineering_handoff"])
                if h["status"] != "completed":
                    return data
                result = h["result"]
                if result.get("status") != "implemented":
                    self.store.transition_task(task_id, "FAILED", task["version"])
                    data["phase"] = "failed"
                else:
                    qa = enqueue_independent_qa(
                        self.store, h["id"], data["spec"]["engineering"], data["spec"]["qa"]
                    )
                    task = self.store.get_task(task_id)
                    self.store.transition_task(task_id, "QA_REVIEW", task["version"])
                    data.update(phase="qa", qa_handoff=qa["id"], revision=result["final_revision"])
            if data["phase"] == "qa":
                h = self.store.get_handoff(data["qa_handoff"])
                if h["status"] == "completed":
                    try:
                        self._validate_qa(data, h)
                    except PermissionError:
                        self.store.transition_task(task_id, "FAILED", task["version"])
                        data["phase"] = "failed"
                    else:
                        task = self.store.transition_task(task_id, "PASSED", task["version"])
                        self.store.transition_task(task_id, "HUMAN_REVIEW", task["version"])
                        data["phase"] = "human_review"
            if data["phase"] == "release":
                h = self.store.get_handoff(data["release_handoff"])
                if (
                    h["status"] == "completed"
                    and h["result"].get("status") != "completed"
                    and task["status"] != "PUBLISHING"
                ):
                    self.store.transition_task(task_id, "FAILED", task["version"])
                    data["phase"] = "failed"
            self._save(task_id, data)
            return data

    def _validate_qa(self, data, handoff):
        result = handoff.get("result") or {}
        commands = result.get("commands") or []
        if not (
            handoff["status"] == "completed"
            and handoff["sender"] == data["spec"]["engineering"]
            and handoff["recipient"] == data["spec"]["qa"]
            and handoff["id"] == qa_handoff_id(data["engineering_handoff"], data["revision"])
            and result.get("status") == "verified_success"
            and result.get("implementation_assignment")
            == assignment_id(data["engineering_handoff"])
            and result.get("tested_revision") == data["revision"]
            and commands
            and all(
                c.get("exit_code") == 0
                and c.get("timed_out") is False
                and c.get("revision") == data["revision"]
                for c in commands
            )
        ):
            raise PermissionError("Independent successful revision-bound QA required")
        return result

    def evidence(self, task_id):
        data = self.get(task_id)
        if not data or not data.get("qa_handoff"):
            raise PermissionError("QA evidence required")
        return self._validate_qa(data, self.store.get_handoff(data["qa_handoff"]))

    def require_release(self, state, agent):
        data = self.get(state["task_id"])
        task = self.store.get_task(state["task_id"])
        approval = (data or {}).get("release_approval", {})
        if not (
            data
            and data["phase"] == "release"
            and task["status"] in {"PUBLISH_APPROVED", "PUBLISHING"}
            and self.parent_active(task)
            and data["spec"]["release"] == agent
            and data.get("release_handoff") == state["handoff_id"]
            and approval.get("approved") is True
            and approval.get("revision") == data["revision"]
        ):
            raise PermissionError("Revision-bound human release approval required")
        self.evidence(state["task_id"])
        return data

    def start_release(self, state, agent):
        with self.store._lock, self.store.transaction():
            self.require_release(state, agent)
            task = self.store.get_task(state["task_id"])
            if task["status"] == "PUBLISH_APPROVED":
                self.store.transition_task(task["id"], "PUBLISHING", task["version"])

    def finish_release(self, task_id, receipt):
        with self.store._lock, self.store.transaction():
            data = self.get(task_id)
            if data.get("receipt"):
                return data["receipt"]
            task = self.store.get_task(task_id)
            self.require_release(
                {"task_id": task_id, "handoff_id": data["release_handoff"]}, data["spec"]["release"]
            )
            task = self.store.transition_task(task_id, "PR_OPEN", task["version"])
            data.update(phase="pr_open", receipt=receipt)
            self._save(task_id, data)
            self.store._event(task_id, "release.receipt", receipt, task["version"])
            text = (
                f"Draft PR release receipt\nCommit: {receipt['commit_sha']}\n"
                f"Engineering approval: {receipt['engineering_approval']['actor']}\n"
                f"Release approval: {receipt['approval']['actor']} "
                f"at {receipt['approval']['decided_at']}\nPR: {receipt['pr_url']}\nTests:\n"
                + "\n".join(
                    f"{encode(test['command'])}: exit {test['exit_code']}, "
                    f"timeout={test['timed_out']}"
                    for test in receipt["tests"]
                )
            )
            for index, offset in enumerate(range(0, len(text), 1600)):
                self.store._enqueue(
                    task_id,
                    "discord",
                    {
                        "type": "release_receipt",
                        "text": text[offset : offset + 1600],
                        "worker_sender": data["spec"]["pm"],
                    },
                    f"{task_id}:release_receipt:{index}",
                )
            return receipt
