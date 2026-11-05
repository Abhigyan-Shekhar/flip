"""Human-authorized, exact-SHA GitHub merges with durable uncertainty handling.

This service has no model tool interface. Its actor must come from an authenticated
human transport; callers must never derive it from repository or model content.
"""

import json
import re
from datetime import UTC, datetime

from .store import ConflictError, encode

_SHA = re.compile(r"^[0-9a-f]{40}$")


class GuardedMerge:
    def __init__(self, workflow, publisher, required_checks):
        if (
            not isinstance(required_checks, (list, tuple))
            or not required_checks
            or len(required_checks) > 100
            or any(not isinstance(c, str) or not c.strip() for c in required_checks)
            or len(set(required_checks)) != len(required_checks)
        ):
            raise ValueError("Explicit required GitHub check names are required")
        self.workflow, self.store, self.publisher = workflow, workflow.store, publisher
        self.required_checks = tuple(required_checks)
        with self.store._lock:
            self.store.connection.execute(
                "CREATE TABLE IF NOT EXISTS guarded_merges "
                "(task_id TEXT PRIMARY KEY REFERENCES tasks(id), data TEXT NOT NULL)"
            )

    def get(self, task_id):
        with self.store._lock:
            row = self.store.connection.execute(
                "SELECT data FROM guarded_merges WHERE task_id=?", (task_id,)
            ).fetchone()
            return json.loads(row[0]) if row else {"version": 0, "phase": "awaiting_approval"}

    def _save(self, task_id, data):
        self.store.connection.execute(
            "INSERT INTO guarded_merges VALUES(?,?) "
            "ON CONFLICT(task_id) DO UPDATE SET data=excluded.data",
            (task_id, encode(data)),
        )

    def _human(self, actor):
        if actor not in self.workflow.approvers:
            raise PermissionError("An authenticated human approver is required")

    def _context(self, task_id):
        data = self.workflow.get(task_id)
        task = self.store.get_task(task_id)
        receipt = (data or {}).get("receipt", {})
        if (
            not data
            or data.get("phase") != "pr_open"
            or not task
            or task["status"] != "PR_OPEN"
            or data["spec"]["repository"] != self.publisher.repository
            or data["spec"]["base"] != self.publisher.base
            or receipt.get("commit_sha") != data.get("revision")
            or not _SHA.fullmatch(str(data.get("revision", "")))
        ):
            raise PermissionError("A published, independently verified task is required")
        match = re.fullmatch(
            r"https://github\.com/" + re.escape(self.publisher.repository) + r"/pull/([1-9][0-9]*)",
            receipt.get("pr_url", ""),
            re.IGNORECASE,
        )
        if not match:
            raise PermissionError("Published PR is outside the configured repository")
        self.workflow.evidence(task_id)
        # Publishing remains backward compatible with optional Security policy.
        # Merging always requires an independently executed, current-SHA gate.
        gate = self.store.connection.execute(
            "SELECT security_recipient,status FROM security_gates "
            "WHERE task_id=? AND engineering_handoff_id=? AND revision=?",
            (task_id, data.get("engineering_handoff"), data["revision"]),
        ).fetchone()
        identities = {
            data["spec"][role] for role in ("pm", "engineering", "qa", "reviewer", "release")
        }
        if not gate or gate["status"] != "passed" or gate["security_recipient"] in identities:
            raise PermissionError("Independent passing Security gate is mandatory for merge")
        return data, int(match[1])

    async def _pull(self, task_id):
        data, number = self._context(task_id)
        pr = await self.publisher.request("GET", f"pulls/{number}")
        if not isinstance(pr, dict) or not (
            pr.get("number") == number
            and pr.get("head", {}).get("repo", {}).get("full_name", "").lower()
            == self.publisher.repository.lower()
            and pr.get("base", {}).get("repo", {}).get("full_name", "").lower()
            == self.publisher.repository.lower()
            and pr.get("base", {}).get("ref") == self.publisher.base
            and _SHA.fullmatch(str(pr.get("base", {}).get("sha", "")))
        ):
            raise PermissionError("GitHub PR destination cannot be verified")
        if pr.get("head", {}).get("sha") != data["revision"]:
            with self.store._lock, self.store.transaction():
                current = self.get(task_id)
                if current.get("approval") and current["phase"] != "merging":
                    current.update(phase="stale", approval=None, version=current["version"] + 1)
                    self._save(task_id, current)
                    self.store._event(
                        task_id,
                        "merge.approval_invalidated",
                        {"reason": "head_changed", "expected_revision": data["revision"]},
                        self.store.get_task(task_id)["version"],
                    )
            raise PermissionError(
                "PR head changed; fresh Engineering, QA, Security and review required"
            )
        return data, number, pr

    async def inspect(self, task_id):
        data, number, pr = await self._pull(task_id)
        revision = data["revision"]
        implementation = self.store.get_handoff(data["engineering_handoff"])["result"]
        if pr["base"]["sha"] != implementation.get("base_revision"):
            raise PermissionError("GitHub base advanced; rebase and fresh validation required")
        if pr.get("state") != "open" or pr.get("draft") is not False:
            raise PermissionError("PR must be open and explicitly marked ready for review")
        if pr.get("mergeable") is not True or pr.get("mergeable_state") != "clean":
            raise PermissionError("GitHub mergeability is pending, blocked or not clean")
        checks = await self.publisher.request(
            "GET", f"commits/{revision}/check-runs", params={"per_page": 100, "filter": "latest"}
        )
        statuses = await self.publisher.request(
            "GET", f"commits/{revision}/status", params={"per_page": 100}
        )
        if (
            not isinstance(checks, dict)
            or not isinstance(statuses, dict)
            or type(checks.get("total_count")) is not int
            or type(statuses.get("total_count")) is not int
            or checks["total_count"] > 100
            or statuses["total_count"] > 100
            or not isinstance(checks.get("check_runs"), list)
            or not isinstance(statuses.get("statuses"), list)
            or len(checks["check_runs"]) != checks["total_count"]
            or len(statuses["statuses"]) != statuses["total_count"]
        ):
            raise PermissionError(
                "GitHub check coverage is unavailable or exceeds bounded pagination"
            )
        passed = set()
        for check in checks["check_runs"]:
            if (
                not isinstance(check, dict)
                or not isinstance(check.get("name"), str)
                or not check["name"]
                or check.get("head_sha") != revision
                or check.get("status") != "completed"
                or check.get("conclusion") != "success"
            ):
                raise PermissionError("GitHub check is stale, pending or unsuccessful")
            passed.add(check.get("name"))
        for status in statuses["statuses"]:
            if (
                not isinstance(status, dict)
                or not isinstance(status.get("context"), str)
                or not status["context"]
                or status.get("state") != "success"
            ):
                raise PermissionError("GitHub commit status is pending or unsuccessful")
            passed.add(status.get("context"))
        if not set(self.required_checks).issubset(passed):
            raise PermissionError("Required GitHub checks are missing")
        return {
            "task_id": task_id,
            "pr_number": number,
            "revision": revision,
            "base_revision": pr["base"]["sha"],
            "required_checks": list(self.required_checks),
            "checks": sorted(passed),
            "merge": self.get(task_id),
        }

    async def approve(self, task_id, actor, expected_version, revision):
        self._human(actor)
        evidence = await self.inspect(task_id)
        if evidence["revision"] != revision:
            raise PermissionError("Human approval must name the verified full head SHA")
        with self.store._lock, self.store.transaction():
            current = self.get(task_id)
            if type(expected_version) is not int or current["version"] != expected_version:
                raise ConflictError("Merge approval version changed")
            if current["phase"] in {"merging", "merged"}:
                raise ConflictError("Merge has already been claimed")
            approval = {
                key: evidence[key]
                for key in ("pr_number", "revision", "base_revision", "required_checks")
            }
            approval.update(actor=actor, approved_at=datetime.now(UTC).isoformat())
            current.update(phase="approved", approval=approval, version=current["version"] + 1)
            self._save(task_id, current)
            self.store._event(
                task_id, "merge.approved", approval, self.store.get_task(task_id)["version"]
            )
            return current

    async def execute(self, task_id, actor, revision):
        self._human(actor)
        current = self.get(task_id)
        if current.get("receipt"):
            if current["receipt"]["revision"] != revision:
                raise PermissionError("Merge revision differs")
            return current["receipt"]
        approval = current.get("approval") or {}
        if approval.get("revision") != revision or current["phase"] not in {"approved", "merging"}:
            raise PermissionError("Separate exact-SHA human merge approval required")
        if current["phase"] == "merging":
            # A network timeout can hide a successful merge. Never repeat PUT.
            _, _, pr = await self._pull(task_id)
            if pr.get("merged") is not True:
                return {"status": "pending_reconciliation", "revision": revision}
            return self._finish(task_id, current, pr.get("merge_commit_sha"), actor)
        evidence = await self.inspect(task_id)
        if any(
            approval.get(key) != evidence[key]
            for key in ("pr_number", "revision", "base_revision", "required_checks")
        ):
            raise PermissionError("PR, base or check policy changed since merge approval")
        with self.store._lock, self.store.transaction():
            latest = self.get(task_id)
            if latest != current:
                raise ConflictError("Merge state changed")
            latest.update(phase="merging", version=latest["version"] + 1, requested_by=actor)
            self._save(task_id, latest)
            current = latest
            self.store._event(
                task_id,
                "merge.claimed",
                {"revision": revision, "actor": actor},
                self.store.get_task(task_id)["version"],
            )
        result = await self.publisher.request(
            "PUT",
            f"pulls/{evidence['pr_number']}/merge",
            json={"sha": revision, "merge_method": "merge"},
        )
        if not isinstance(result, dict) or result.get("merged") is not True:
            return {"status": "pending_reconciliation", "revision": revision}
        return self._finish(task_id, current, result.get("sha"), actor)

    def _finish(self, task_id, current, merge_sha, actor):
        if not isinstance(merge_sha, str) or not _SHA.fullmatch(merge_sha):
            raise PermissionError("GitHub merge commit receipt cannot be verified")
        with self.store._lock, self.store.transaction():
            latest = self.get(task_id)
            if latest.get("receipt"):
                return latest["receipt"]
            receipt = {
                "status": "merged",
                "task_id": task_id,
                "revision": current["approval"]["revision"],
                "merge_commit_sha": merge_sha,
                "approval": current["approval"],
                "requested_by": current.get("requested_by", current["approval"]["actor"]),
                "confirmed_by": actor,
                "merged_at": datetime.now(UTC).isoformat(),
            }
            latest.update(phase="merged", receipt=receipt, version=latest["version"] + 1)
            self._save(task_id, latest)
            task = self.store.get_task(task_id)
            self.store._event(task_id, "merge.receipt", receipt, task["version"])
            self.store.transition_task(task_id, "COMPLETED", task["version"])
            data = self.workflow.get(task_id)
            data.update(phase="merged", merge_receipt=receipt)
            self.workflow._save(task_id, data)
            self.store._enqueue(
                task_id,
                "discord",
                {
                    "type": "merge_receipt",
                    "worker_sender": data["spec"]["pm"],
                    "text": f"PR merged after human approval. Head: {receipt['revision']}\n"
                    f"Merge commit: {merge_sha}",
                },
                f"{task_id}:merge_receipt",
            )
            return receipt
