"""Optional, revision-bound security gates for Engineering assignments."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from .engineering import assignment_id
from .store import ConflictError

_REVISION = re.compile(r"^[0-9a-f]{40,64}$")
_SEVERITY = {"critical": 4, "high": 3, "medium": 2, "low": 1, "info": 0}


def _ensure_table(store):
    with store._lock:
        store.connection.execute(
            "CREATE TABLE IF NOT EXISTS security_gates ("
            "task_id TEXT NOT NULL, engineering_handoff_id TEXT NOT NULL, "
            "revision TEXT NOT NULL, security_task_id TEXT NOT NULL UNIQUE, "
            "security_handoff_id TEXT NOT NULL UNIQUE, security_recipient TEXT NOT NULL, "
            "policy TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', "
            "PRIMARY KEY(task_id, engineering_handoff_id, revision))"
        )
        store.connection.execute(
            "CREATE TABLE IF NOT EXISTS project_security_policies ("
            "project_id TEXT PRIMARY KEY, pm_id TEXT NOT NULL, security_id TEXT NOT NULL, "
            "policy TEXT NOT NULL)"
        )


def configure_security_policy(store, project_id, pm_id, security_id, policy):
    """Persist a project gate policy; policy changes require an explicit operator migration."""
    if not all(
        isinstance(value, str) and value.strip() for value in (project_id, pm_id, security_id)
    ):
        raise ValueError("Project, PM, and Security identities are required")
    normalized = _valid_policy(policy)
    encoded = json.dumps(normalized, sort_keys=True)
    _ensure_table(store)
    with store.transaction():
        existing = store.connection.execute(
            "SELECT pm_id,security_id,policy FROM project_security_policies WHERE project_id=?",
            (project_id,),
        ).fetchone()
        if existing:
            if (existing["pm_id"], existing["security_id"], existing["policy"]) != (
                pm_id,
                security_id,
                encoded,
            ):
                raise ConflictError(
                    "Registered project security policy cannot be weakened or replaced"
                )
        else:
            store.connection.execute(
                "INSERT INTO project_security_policies(project_id,pm_id,security_id,policy) "
                "VALUES(?,?,?,?)",
                (project_id, pm_id, security_id, encoded),
            )
    return {
        "project_id": project_id,
        "pm_id": pm_id,
        "security_id": security_id,
        "policy": normalized,
    }


def _configured_policy(store, task):
    _ensure_table(store)
    project_id = (task or {}).get("source", {}).get("project_id")
    if not isinstance(project_id, str) or not project_id:
        return None
    row = store.connection.execute(
        "SELECT * FROM project_security_policies WHERE project_id=?", (project_id,)
    ).fetchone()
    if not row:
        return None
    result = dict(row)
    result["policy"] = json.loads(result["policy"])
    return result


def _row(store, task_id, engineering_handoff_id, revision):
    _ensure_table(store)
    row = store.connection.execute(
        "SELECT * FROM security_gates WHERE task_id=? AND engineering_handoff_id=? AND revision=?",
        (task_id, engineering_handoff_id, revision),
    ).fetchone()
    if not row:
        return None
    result = dict(row)
    result["policy"] = json.loads(result["policy"])
    return result


def _valid_policy(policy):
    if not isinstance(policy, dict):
        raise ValueError("Security gate policy must be an object")
    threshold = str(policy.get("block_at_or_above", "high")).lower()
    roots = policy.get("allowed_repo_roots", [])
    if threshold not in {"high", "critical"} or not isinstance(roots, list) or not roots:
        raise ValueError("Security gate requires a severity threshold and allowed repository roots")
    resolved = []
    for root in roots:
        path = Path(root).resolve(strict=True)
        if not path.is_dir():
            raise ValueError("Allowed repository roots must be existing directories")
        resolved.append(str(path))
    return {**policy, "block_at_or_above": threshold, "allowed_repo_roots": resolved}


def _within_allowed_root(checkout, roots):
    try:
        resolved = Path(checkout).resolve(strict=True)
        return any(resolved.is_relative_to(Path(root).resolve(strict=True)) for root in roots)
    except (OSError, RuntimeError, ValueError):
        return False


def _engineering_result(store, task_id, workflow_data):
    if not isinstance(workflow_data, dict):
        raise ValueError("Approval workflow data is required")
    handoff_id = workflow_data.get("engineering_handoff")
    revision = workflow_data.get("revision")
    handoff = store.get_handoff(handoff_id) if handoff_id else None
    result = (handoff or {}).get("result")
    if (
        not handoff
        or handoff["task_id"] != task_id
        or handoff["status"] != "completed"
        or not isinstance(result, dict)
        or result.get("status") != "implemented"
        or result.get("assignment_id") != assignment_id(handoff_id)
        or not isinstance(revision, str)
        or not _REVISION.fullmatch(revision)
        or result.get("final_revision") != revision
    ):
        raise ValueError("No completed Engineering result exists for the current revision")
    checkout = result.get("checkout")
    if not isinstance(checkout, str) or not Path(checkout).is_absolute():
        raise ValueError("Engineering result has no absolute checkout path")
    return handoff, result, revision, Path(checkout).resolve(strict=True)


def schedule_security_gate(store, task_id, pm_id, security_id, policy):
    """Create one independent Security handoff for the current Engineering SHA.

    Call this from the application poll after Engineering completion. It can be
    scheduled through the pre-release workflow phases and never changes the
    parent workflow phase or release spec.
    """
    if not all(isinstance(v, str) and v.strip() for v in (pm_id, security_id)):
        raise ValueError("PM and Security identities are required")
    policy = _valid_policy(policy)
    workflow_row = store.connection.execute(
        "SELECT data FROM approval_workflows WHERE task_id=?", (task_id,)
    ).fetchone()
    if not workflow_row:
        raise ValueError("Approval workflow is unavailable")
    workflow_data = json.loads(workflow_row[0])
    parent = store.get_task(task_id)
    configured = _configured_policy(store, parent)
    if configured and (
        configured["pm_id"] != pm_id
        or configured["security_id"] != security_id
        or configured["policy"] != policy
    ):
        raise ConflictError("Security gate does not match the registered project policy")
    if workflow_data.get("spec", {}).get("pm") != pm_id:
        raise PermissionError("Security gate sender must match the configured PM")
    if workflow_data.get("phase") not in {
        "engineering",
        "qa",
        "review",
        "human_review",
        "release",
    } or parent["status"] not in {"IN_PROGRESS", "QA_REVIEW", "HUMAN_REVIEW", "PUBLISH_APPROVED"}:
        raise ValueError("Security gate must be scheduled before QA review completes")
    engineering, result, revision, checkout = _engineering_result(store, task_id, workflow_data)
    if not _within_allowed_root(checkout, policy["allowed_repo_roots"]):
        raise PermissionError("Engineering checkout is outside the configured security roots")
    prior = _row(store, task_id, engineering["id"], revision)
    if prior:
        if prior["security_recipient"] != security_id or prior["policy"] != policy:
            raise ConflictError("Security gate assignment already exists with different policy")
        return prior

    identity = hashlib.sha256(f"{task_id}:{engineering['id']}:{revision}".encode()).hexdigest()
    child_task_id = str(uuid5(NAMESPACE_URL, f"switchcrew:security-gate:task:{identity}"))
    child_handoff_id = str(uuid5(NAMESPACE_URL, f"switchcrew:security-gate:handoff:{identity}"))
    source = {
        "security_gate": True,
        "source_evidence_task_id": task_id,
        "engineering_handoff_id": engineering["id"],
        "revision": revision,
        "checkout": str(checkout),
        "policy": policy,
    }
    task = store.create_task(
        f"Read-only security scan for {task_id} at {revision}",
        source,
        ["Run configured security scanners against the exact pinned revision."],
        task_id=child_task_id,
    )
    question = (
        f"Run the configured read-only security scan for revision {revision}. "
        "Use only the authorized local checkout and operator scanner configuration."
    )
    handoff = store.create_handoff(
        task["id"],
        pm_id,
        security_id,
        question,
        task["acceptance_criteria"],
        task["version"],
        handoff_id=child_handoff_id,
    )
    with store.transaction():
        _ensure_table(store)
        store.connection.execute(
            "INSERT INTO security_gates(task_id,engineering_handoff_id,revision,security_task_id,"
            "security_handoff_id,security_recipient,policy,status) VALUES(?,?,?,?,?,?,?,'pending')",
            (
                task_id,
                engineering["id"],
                revision,
                task["id"],
                handoff["id"],
                security_id,
                json.dumps(policy, sort_keys=True),
            ),
        )
    return _row(store, task_id, engineering["id"], revision)


def require_security_gate(store, task_id, workflow_data):
    """Raise unless an existing optional gate passes on the current SHA."""
    _ensure_table(store)
    parent_task = store.get_task(task_id)
    configured = _configured_policy(store, parent_task)
    try:
        engineering, _result, revision, _checkout = _engineering_result(
            store, task_id, workflow_data
        )
    except (ValueError, OSError):
        recorded_gate = store.connection.execute(
            "SELECT 1 FROM security_gates WHERE task_id=? LIMIT 1", (task_id,)
        ).fetchone()
        if configured or recorded_gate:
            raise PermissionError(
                "Current Engineering revision cannot be verified for security gate"
            ) from None
        return True
    gate = _row(store, task_id, engineering["id"], revision)
    if not gate:
        if configured:
            raise PermissionError(
                "Configured project requires a security gate for the current revision"
            )
        return True
    if configured and (
        gate["security_recipient"] != configured["security_id"]
        or gate["policy"] != configured["policy"]
    ):
        raise PermissionError("Security gate does not match the registered project policy")
    handoff = store.get_handoff(gate["security_handoff_id"])
    child = store.get_task(gate["security_task_id"])
    source = (child or {}).get("source", {})
    if (
        not handoff
        or not child
        or handoff["task_id"] != gate["security_task_id"]
        or handoff["recipient"] != gate["security_recipient"]
        or handoff["sender"] != workflow_data.get("spec", {}).get("pm")
        or source.get("source_evidence_task_id") != task_id
        or source.get("engineering_handoff_id") != engineering["id"]
        or source.get("revision") != revision
        or source.get("security_gate") is not True
        or gate["task_id"] != task_id
        or gate["engineering_handoff_id"] != engineering["id"]
        or gate["revision"] != revision
        or handoff["status"] != "completed"
    ):
        raise PermissionError("Current revision security gate is pending or invalid")
    report = handoff.get("result")
    if (
        not isinstance(report, dict)
        or report.get("status") not in {"verified_success", "findings"}
        or report.get("revision") != revision
        or not isinstance(report.get("findings"), list)
        or not isinstance(report.get("scanners"), list)
        or not report.get("scanners")
        or any(
            not isinstance(scanner, dict) or scanner.get("status") != "completed"
            for scanner in report.get("scanners", [])
        )
        or report.get("warnings")
    ):
        raise PermissionError("Current revision has no successful configured security scan")
    threshold = _SEVERITY[gate["policy"]["block_at_or_above"]]
    for finding in report["findings"]:
        if not isinstance(finding, dict):
            raise PermissionError("Security findings are malformed")
        severity = _SEVERITY.get(str(finding.get("severity", "")).lower())
        if severity is None or severity >= threshold:
            raise PermissionError("Security findings meet or exceed the release blocking threshold")
    return True


class GateSecurityExecutor:
    """Validate a persisted gate assignment then run SecurityRunner at its SHA."""

    def __init__(self, store, recipient, runner):
        self.store = store
        self.recipient = recipient
        self.runner = runner

    async def __call__(self, state):
        handoff_id = state.get("handoff_id") if isinstance(state, dict) else None
        handoff = self.store.get_handoff(handoff_id) if handoff_id else None
        task = self.store.get_task(handoff["task_id"]) if handoff else None
        source = (task or {}).get("source", {})
        if (
            not handoff
            or handoff["recipient"] != self.recipient
            or handoff["status"] not in {"pending", "accepted"}
            or not source.get("security_gate")
            or state.get("task_id") != task["id"]
        ):
            return {"status": "blocked", "reason": "security_gate_scope_denied"}
        gate = self.store.connection.execute(
            "SELECT * FROM security_gates WHERE security_task_id=? AND security_handoff_id=?",
            (task["id"], handoff_id),
        ).fetchone()
        if not gate or gate["security_recipient"] != self.recipient:
            return {"status": "blocked", "reason": "security_gate_mapping_missing"}
        allowed = json.loads(gate["policy"])["allowed_repo_roots"]
        checkout = Path(source.get("checkout", "")).resolve()
        revision = source.get("revision")
        if (
            not _within_allowed_root(checkout, allowed)
            or source.get("source_evidence_task_id") != gate["task_id"]
            or source.get("engineering_handoff_id") != gate["engineering_handoff_id"]
            or source.get("revision") != gate["revision"]
            or source.get("checkout") != str(checkout)
        ):
            return {"status": "blocked", "reason": "security_gate_checkout_denied"}
        before = self._current_assignment(gate, source, checkout, revision)
        if before is not None:
            return {"status": "blocked", "reason": before}
        report = await self.runner.run(checkout, revision)
        after = self._current_assignment(gate, source, checkout, revision)
        if after is not None:
            return {"status": "blocked", "reason": after}
        return report.to_dict()

    def _current_assignment(self, gate, source, checkout, revision):
        """Recheck parent state around scanning to reject cancellation and stale work."""
        parent = self.store.get_task(gate["task_id"])
        if not parent or parent["status"] not in {
            "IN_PROGRESS",
            "QA_REVIEW",
            "HUMAN_REVIEW",
            "PUBLISH_APPROVED",
        }:
            return "security_gate_parent_inactive"
        row = self.store.connection.execute(
            "SELECT data FROM approval_workflows WHERE task_id=?", (gate["task_id"],)
        ).fetchone()
        if not row:
            return "security_gate_workflow_missing"
        current = json.loads(row[0])
        try:
            engineering, _result, current_revision, current_checkout = _engineering_result(
                self.store, gate["task_id"], current
            )
        except (ValueError, OSError):
            return "security_gate_engineering_unavailable"
        if (
            engineering["id"] != gate["engineering_handoff_id"]
            or current_revision != revision
            or current_revision != gate["revision"]
            or current_checkout != checkout
            or source.get("checkout") != str(current_checkout)
        ):
            return "security_gate_assignment_stale"
        if not _within_allowed_root(
            current_checkout, json.loads(gate["policy"])["allowed_repo_roots"]
        ):
            return "security_gate_checkout_denied"
        from .engineering import git

        try:
            if git(current_checkout, "rev-parse", "HEAD") != current_revision:
                return "security_gate_revision_mismatch"
        except Exception:
            return "security_gate_checkout_unavailable"
        return None
