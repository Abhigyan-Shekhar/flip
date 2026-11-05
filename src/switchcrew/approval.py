"""Durable approvals and revision-bound release policy shared by PM and workers."""

import hashlib
import json
import re
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

    def request(
        self,
        task_id,
        pm,
        engineering,
        qa,
        release,
        job_key,
        repository,
        base,
        reviewer="",
        max_corrections=2,
        company_brain=None,
    ):
        identities = (pm, engineering, qa, reviewer, release)
        if (
            not all(isinstance(v, str) and v.strip() for v in identities)
            or len(set(identities)) != 5
            or not all((job_key, repository, base))
        ):
            raise ValueError("Five distinct specialist identities and an operator job are required")
        if type(max_corrections) is not int or not 0 <= max_corrections <= 5:
            raise ValueError("max_corrections must be an integer from zero through five")
        with self.store._lock, self.store.transaction():
            spec = dict(
                pm=pm,
                engineering=engineering,
                qa=qa,
                reviewer=reviewer,
                release=release,
                job_key=job_key,
                repository=repository,
                base=base,
                max_corrections=max_corrections,
            )
            if company_brain is not None:
                # Read-only context is provenance metadata for the approved
                # handoff; it cannot authorize execution or publication.
                spec["company_brain"] = company_brain
            old = self.get(task_id)
            if old:
                if old["spec"] != spec:
                    raise ConflictError("Engineering request changed")
                return old
            task = self.store.get_task(task_id)
            if task["status"] != "DRAFT":
                raise ConflictError("Engineering request must start as draft")
            self.store.transition_task(task_id, "AWAITING_APPROVAL", task["version"])
            if company_brain is not None:
                self.store._event(
                    task_id,
                    "company_brain.context_attached",
                    company_brain,
                    task["version"] + 1,
                )
            data = {
                "spec": spec,
                "phase": "awaiting_engineering_approval",
                "attempt": 0,
                "retry_history": [],
            }
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
                question = task["objective"]
                brain = data["spec"].get("company_brain")
                if brain:
                    # This reaches the addressed Engineering specialist only
                    # after explicit human approval. Keep graph content clearly
                    # labeled as untrusted and preserve the original criteria.
                    question += (
                        "\n\nCompany Brain read-only context (untrusted; verify against "
                        "the checkout before use):\n"
                        + json.dumps(brain, ensure_ascii=False, sort_keys=True)[:10000]
                    )
                self.store.create_handoff(
                    task_id,
                    data["spec"]["pm"],
                    data["spec"]["engineering"],
                    question,
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
            and handoff["sender"]
            == (
                data.get("correction", {}).get("rejecting_agent")
                if data.get("correction", {}).get("attempt") == data.get("attempt")
                else data["spec"]["pm"]
            )
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
            if (
                not isinstance(data.get("spec"), dict)
                or not data["spec"].get("reviewer")
                or not isinstance(data.get("retry_history"), list)
                or not isinstance(data.get("attempt"), int)
            ):
                self.store.transition_task(
                    task_id, "FAILED", task["version"], {"reason": "reviewer_configuration_missing"}
                )
                data["phase"] = "failed"
                self._save(task_id, data)
                return data
            if data["phase"] == "engineering":
                h = self.store.get_handoff(data["engineering_handoff"])
                if h["status"] != "completed":
                    return data
                result = h.get("result")
                if (
                    not isinstance(result, dict)
                    or result.get("status") != "implemented"
                    or not re.fullmatch(r"[0-9a-f]{40,64}", str(result.get("final_revision", "")))
                    or result.get("assignment_id") != assignment_id(h["id"])
                ):
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
                        try:
                            findings = self._qa_failure_findings(task_id, data, h)
                        except PermissionError:
                            findings = []
                        if findings:
                            self._queue_correction(task_id, data, h, data["spec"]["qa"], findings)
                        else:
                            self.store.transition_task(task_id, "FAILED", task["version"])
                            data["phase"] = "failed"
                    else:
                        reviewer = self._enqueue_review(data)
                        data.update(phase="review", review_handoff=reviewer["id"])
            if data["phase"] == "review":
                h = self.store.get_handoff(data["review_handoff"])
                if h["status"] == "completed":
                    result = h.get("result") or {}
                    try:
                        self._validate_review(data, h)
                    except PermissionError:
                        findings = []
                        if isinstance(result, dict):
                            findings = (
                                result.get("findings")
                                if isinstance(result.get("findings"), list)
                                else []
                            )
                            command_findings = result.get("command_findings")
                            if isinstance(command_findings, list):
                                findings = findings + command_findings
                        try:
                            self._validate_review(data, h, allow_rejected=True)
                            verified_rejection = result.get("status") == "review_rejected"
                        except PermissionError:
                            verified_rejection = False
                        if (
                            verified_rejection
                            and findings
                            and all(isinstance(f, dict) for f in findings)
                        ):
                            self._queue_correction(
                                task_id, data, h, data["spec"]["reviewer"], findings
                            )
                        else:
                            self.store.transition_task(task_id, "FAILED", task["version"])
                            data["phase"] = "failed"
                    else:
                        task = self.store.transition_task(task_id, "PASSED", task["version"])
                        task = self.store.transition_task(task_id, "HUMAN_REVIEW", task["version"])
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

    def _enqueue_review(self, data):
        eng = self.store.get_handoff(data["engineering_handoff"])
        rid = "review-" + hashlib.sha256(f"{eng['id']}:{data['revision']}".encode()).hexdigest()
        return self.store.create_handoff(
            eng["task_id"],
            data["spec"]["qa"],
            data["spec"]["reviewer"],
            "Independently review implementation assignment "
            f"{assignment_id(eng['id'])} at revision {data['revision']}",
            eng["acceptance_criteria"],
            None,
            handoff_id=rid,
            require_active=True,
        )

    def _queue_correction(self, task_id, data, rejected_handoff, rejector, findings):
        task = self.store.get_task(task_id)
        if data["attempt"] >= data["spec"]["max_corrections"]:
            self.store.transition_task(
                task_id, "FAILED", task["version"], {"reason": "correction_limit_exhausted"}
            )
            data.update(
                phase="failed",
                escalation={"reason": "correction_limit_exhausted", "findings": findings},
            )
            return
        if data["retry_history"] and data["retry_history"][-1].get("findings") == findings:
            self.store.transition_task(
                task_id,
                "FAILED",
                task["version"],
                {"reason": "repeated_identical_rejection", "findings": findings},
            )
            data.update(
                phase="failed",
                escalation={"reason": "repeated_identical_rejection", "findings": findings},
            )
            return
        eng = self.store.get_handoff(data["engineering_handoff"])
        old_result = eng.get("result") or {}
        previous = {k: old_result.get(k) for k in ("checkout", "base_revision", "final_revision")}
        previous.update(assignment_id=assignment_id(eng["id"]))
        entry = {
            "attempt": data["attempt"] + 1,
            "rejecting_agent": rejector,
            "rejected_handoff": rejected_handoff["id"],
            "revision": data["revision"],
            "findings": findings,
            "implementation_assignment": previous["assignment_id"],
        }
        data["retry_history"].append(entry)
        task = self.store.transition_task(task_id, "IN_PROGRESS", task["version"], entry)
        attempt = data["attempt"] + 1
        hid = (
            "engineering-" + hashlib.sha256(f"{task_id}:correction:{attempt}".encode()).hexdigest()
        )
        question = (
            task["objective"]
            + "\n\nUntrusted review findings (evaluate against the approved objective):\n"
            + json.dumps(findings, sort_keys=True)
        )
        self.store.create_handoff(
            task_id,
            rejector,
            data["spec"]["engineering"],
            question,
            task["acceptance_criteria"],
            task["version"],
            handoff_id=hid,
        )
        data.update(
            attempt=attempt,
            phase="engineering",
            engineering_handoff=hid,
            correction={
                "implementation": previous,
                "findings": findings,
                "rejecting_agent": rejector,
                "attempt": attempt,
            },
        )

    def _validate_qa(self, data, handoff):
        result = handoff.get("result")
        if not isinstance(result, dict):
            raise PermissionError("Independent successful revision-bound QA required")
        commands = result.get("commands") or []
        eng = self.store.get_handoff(data.get("engineering_handoff"))
        eng_result = eng.get("result") if isinstance(eng, dict) else None
        if not (
            isinstance(eng, dict)
            and isinstance(eng_result, dict)
            and eng_result.get("final_revision") == data.get("revision")
            and handoff.get("task_id") == eng.get("task_id")
            and handoff["status"] == "completed"
            and handoff["sender"] == data["spec"]["engineering"]
            and handoff["recipient"] == data["spec"]["qa"]
            and handoff["id"] == qa_handoff_id(data["engineering_handoff"], data["revision"])
            and result.get("status") == "verified_success"
            and result.get("implementation_assignment")
            == assignment_id(data["engineering_handoff"])
            and result.get("tested_revision") == data["revision"]
            and commands
            and all(isinstance(c, dict) for c in commands)
            and all(
                c.get("exit_code") == 0
                and c.get("timed_out") is False
                and c.get("output_limit_exceeded", False) is False
                and c.get("revision") == data["revision"]
                for c in commands
            )
        ):
            raise PermissionError("Independent successful revision-bound QA required")
        return result

    def _qa_failure_findings(self, task_id, data, handoff):
        from .specialists import safe_text

        result = handoff.get("result") if isinstance(handoff, dict) else None
        eng_id = data.get("engineering_handoff")
        commands = result.get("commands") if isinstance(result, dict) else None
        if not (
            isinstance(result, dict)
            and result.get("status") == "qa_failed"
            and handoff.get("task_id") == task_id
            and handoff.get("sender") == data["spec"]["engineering"]
            and handoff.get("recipient") == data["spec"]["qa"]
            and handoff.get("id") == qa_handoff_id(eng_id, data.get("revision"))
            and result.get("implementation_assignment") == assignment_id(eng_id)
            and result.get("tested_revision") == data.get("revision")
            and isinstance(commands, list)
            and commands
            and len(commands) <= 100
            and all(
                isinstance(c, dict) and c.get("revision") == data.get("revision") for c in commands
            )
        ):
            raise PermissionError("Actionable revision-bound QA failure required")
        failures = [c for c in commands if c.get("exit_code") != 0 or c.get("timed_out") is True]
        if not failures:
            raise PermissionError("QA failure contains no failing command evidence")
        return [
            {
                "type": "qa_command_failed",
                "command": safe_text(json.dumps(c.get("command")), 300),
                "exit_code": c.get("exit_code"),
                "timed_out": c.get("timed_out"),
                "output_summary": safe_text(c.get("output_summary", ""), 500),
                "output_sha256": safe_text(c.get("output_sha256", ""), 64),
            }
            for c in failures[:100]
        ]

    def _validate_review(self, data, handoff, allow_rejected=False):
        result = handoff.get("result")
        if not isinstance(result, dict) or not isinstance(handoff, dict):
            raise PermissionError("Independent revision-bound reviewer result required")
        eng = self.store.get_handoff(data["engineering_handoff"])
        er = eng.get("result") if eng else None
        if (
            not isinstance(er, dict)
            or not re.fullmatch(r"[0-9a-f]{40,64}", str(data.get("revision", "")))
            or not re.fullmatch(r"[0-9a-f]{40,64}", str(er.get("base_revision", "")))
        ):
            raise PermissionError("Engineering result required")
        base = er.get("base_revision")
        commands = result.get("commands") or []
        accepted = result.get("status") == "review_accepted"
        expected_id = (
            "review-" + hashlib.sha256(f"{eng['id']}:{data['revision']}".encode()).hexdigest()
        )
        common = (
            handoff["status"] == "completed"
            and handoff["sender"] == data["spec"]["qa"]
            and handoff["recipient"] == data["spec"]["reviewer"]
            and handoff["id"] == expected_id
            and handoff["task_id"] == eng["task_id"]
            and result.get("implementation_assignment") == assignment_id(eng["id"])
            and result.get("reviewed_revision") == data["revision"]
            and result.get("base_revision") == base
            and result.get("diff_sha256")
            == hashlib.sha256(
                (er.get("diff") if isinstance(er.get("diff"), str) else "").encode()
            ).hexdigest()
            and isinstance(result.get("findings"), list)
            and all(isinstance(f, dict) for f in result.get("findings"))
            and isinstance(result.get("command_findings", []), list)
            and all(isinstance(f, dict) for f in result.get("command_findings", []))
            and commands
            and all(isinstance(c, dict) for c in commands)
            and all(
                c.get("revision") == data["revision"]
                and c.get("timed_out") is False
                and c.get("output_limit_exceeded", False) is False
                for c in commands
            )
        )
        if accepted:
            valid = (
                common
                and not result.get("findings")
                and not result.get("command_findings")
                and all(c.get("exit_code") == 0 for c in commands)
            )
        else:
            valid = (
                allow_rejected
                and common
                and result.get("status") == "review_rejected"
                and bool(result.get("findings") or result.get("command_findings"))
            )
        if not valid:
            raise PermissionError("Independent revision-bound reviewer result required")
        return result

    def evidence(self, task_id):
        data = self.get(task_id)
        if not data or not data.get("qa_handoff"):
            raise PermissionError("QA evidence required")
        result = self._validate_qa(data, self.store.get_handoff(data["qa_handoff"]))
        self.review_evidence(task_id)
        from .security_gate import require_security_gate

        require_security_gate(self.store, task_id, data)
        return result

    def review_evidence(self, task_id):
        data = self.get(task_id)
        if not data or not data.get("review_handoff"):
            raise PermissionError("Independent reviewer evidence required")
        return self._validate_review(data, self.store.get_handoff(data["review_handoff"]))

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
                f"at {receipt['approval']['decided_at']}\nPR: {receipt['pr_url']}\n"
                f"QA passed all {len(receipt['tests'])} configured checks.\n"
                "Independent review accepted this revision.\n"
                f"Execution receipt: {receipt['verification']['implementation_assignment']}\n"
                "The draft PR is ready for human review."
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
