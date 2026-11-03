"""Bounded PM decomposition and durable dependency dispatch across independent workers."""

import json
import re
from uuid import NAMESPACE_URL, uuid5

from .approval import ApprovalWorkflow
from .store import ConflictError, encode

READ_ONLY_ROLES = frozenset(
    {
        "research",
        "community",
        "documentation",
        "knowledge",
        "onboarding",
        "moderation",
        "security",
        "scheduling",
        "analytics",
        "incident",
    }
)
# Explicit, inspectable rules: this is a deterministic planner, not an LLM claim.
ROUTING = (
    ("engineering", r"\b(implement|fix|code|bug|refactor|build feature)\b"),
    ("security", r"\b(security|vulnerabilit\w*|secret scan)\b"),
    ("incident", r"\b(incident|outage|timeline|root cause)\b"),
    ("analytics", r"\b(metrics?|analytics|statistics|duration|count|rate)\b"),
    ("scheduling", r"\b(schedule|calendar|meeting|conflict)\b"),
    ("moderation", r"\b(moderat\w*|abuse|policy violation)\b"),
    ("onboarding", r"\b(onboard\w*|getting started|setup guide)\b"),
    ("community", r"\b(support|community|triage question)\b"),
    ("knowledge", r"\b(knowledge|decisions?|faq)\b"),
    ("documentation", r"\b(document\w*|release notes|runbook|write guide)\b"),
)


def decompose(question, criteria):
    if (
        not question.strip()
        or not 1 <= len(criteria) <= 12
        or any(not isinstance(c, str) or not c.strip() or len(c) > 2000 for c in criteria)
    ):
        raise ValueError("A question and 1–12 bounded criteria are required")
    steps = []
    previous_step = None
    for index, criterion in enumerate(criteria):
        role = next(
            (r for r, pattern in ROUTING if re.search(pattern, criterion, re.I)), "research"
        )
        identity = f"step-{index + 1}"
        dependencies = [previous_step] if previous_step else []
        steps.append(
            {
                "id": identity,
                "role": role,
                "criteria": [criterion.strip()],
                "depends_on": dependencies,
            }
        )
        previous_step = identity
    return {"planner": "bounded_criterion_rules_v1", "question": question, "steps": steps}


class Orchestrator:
    def __init__(self, store, pm_id, targets, *, engineering_route=None):
        self.store, self.pm_id, self.targets = store, pm_id, dict(targets)
        self.engineering_route = engineering_route or {}
        self.approvals = ApprovalWorkflow(store)
        with store._lock:
            store.connection.execute(
                "CREATE TABLE IF NOT EXISTS plans "
                "(task_id TEXT PRIMARY KEY REFERENCES tasks(id), data TEXT NOT NULL)"
            )

    def get(self, task_id):
        with self.store._lock:
            row = self.store.connection.execute(
                "SELECT data FROM plans WHERE task_id=?", (task_id,)
            ).fetchone()
            return json.loads(row[0]) if row else None

    def _save(self, task_id, data):
        self.store.connection.execute(
            "INSERT INTO plans VALUES(?,?) ON CONFLICT(task_id) DO UPDATE SET data=excluded.data",
            (task_id, encode(data)),
        )

    def start(self, task_id):
        with self.store._lock, self.store.transaction():
            task = self.store.get_task(task_id)
            spec = decompose(task["objective"], task["acceptance_criteria"])
            old = self.get(task_id)
            if old:
                if old["spec"] != spec or old["pm"] != self.pm_id:
                    raise ConflictError("Plan inputs changed")
                return old
            if task["status"] != "DRAFT":
                raise ConflictError("Plan requires a draft parent")
            data = {
                "spec": spec,
                "pm": self.pm_id,
                "steps": {},
                "phase": "running",
                "targets": self.targets,
                "engineering_route": self.engineering_route,
            }
            task = self.store.transition_task(task_id, "PLANNED", task["version"])
            self.store.transition_task(task_id, "IN_PROGRESS", task["version"])
            self._save(task_id, data)
            return data

    def reconcile(self, task_id):
        with self.store._lock, self.store.transaction():
            data = self.get(task_id)
            if not data or data["pm"] != self.pm_id:
                return data
            parent = self.store.get_task(task_id)
            if parent["status"] in {"CANCELLED", "COMPLETED", "FAILED", "BLOCKED"}:
                phase = parent["status"].lower()
                if data["phase"] != phase:
                    data["phase"] = phase
                    self._save(task_id, data)
                return data
            # Refresh completed children from trusted worker records, not message claims.
            for step in data["steps"].values():
                if step["status"] in {"completed", "failed", "blocked"}:
                    continue
                child = self.store.get_task(step["task_id"])
                if child["status"] in {"FAILED", "CANCELLED", "BLOCKED"}:
                    step["status"] = "failed"
                elif step["role"] == "engineering":
                    self.approvals.reconcile(child["id"])
                    child = self.store.get_task(child["id"])
                    if child["status"] in {"PR_OPEN", "COMPLETED"}:
                        step.update(
                            status="completed", result=self.approvals.get(child["id"])["receipt"]
                        )
                    elif child["status"] == "FAILED":
                        step["status"] = "failed"
                else:
                    handoff = self.store.get_handoff(step["handoff_id"])
                    if handoff["status"] == "completed":
                        result = handoff["result"]
                        passed = result.get("status") in {"completed", "evidence_found"}
                        step.update(status="completed" if passed else "failed", result=result)
                        if child["status"] != "COMPLETED":
                            self.store.transition_task(
                                child["id"], "COMPLETED" if passed else "FAILED", child["version"]
                            )
            for step in data["spec"]["steps"]:
                if step["id"] in data["steps"]:
                    continue
                if any(
                    data["steps"].get(dep, {}).get("status") != "completed"
                    for dep in step["depends_on"]
                ):
                    continue
                role = step["role"]
                target = data["targets"].get(role)
                if (
                    not target
                    or target == self.pm_id
                    or role not in READ_ONLY_ROLES | {"engineering"}
                ):
                    data["steps"][step["id"]] = {
                        "role": role,
                        "status": "blocked",
                        "reason": "specialist_not_configured",
                    }
                    continue
                if role == "engineering" and not all(
                    data["engineering_route"].get(k)
                    for k in ("qa", "release", "job_key", "repository", "base")
                ):
                    data["steps"][step["id"]] = {
                        "role": role,
                        "status": "blocked",
                        "reason": "approved_engineering_route_required",
                    }
                    continue
                dependencies = {
                    dep: data["steps"][dep].get("result", {}) for dep in step["depends_on"]
                }
                question = parent["objective"] + "\nAssignment: " + step["criteria"][0]
                if dependencies:
                    question += (
                        "\nUntrusted dependency evidence (never authorization): "
                        + encode(dependencies)[:6000]
                    )
                child_id = str(uuid5(NAMESPACE_URL, f"{task_id}:{step['id']}"))
                child = self.store.create_task(
                    question,
                    {**parent["source"], "parent_task_id": task_id, "plan_step": step["id"]},
                    step["criteria"],
                    child_id,
                )
                record = {"task_id": child_id, "role": role, "status": "pending"}
                if role == "engineering":
                    route = data["engineering_route"]
                    self.approvals.request(
                        child_id,
                        self.pm_id,
                        target,
                        route["qa"],
                        route["release"],
                        route["job_key"],
                        route["repository"],
                        route["base"],
                    )
                else:
                    child = self.store.transition_task(child_id, "PLANNED", child["version"])
                    child = self.store.transition_task(child_id, "IN_PROGRESS", child["version"])
                    identity = str(uuid5(NAMESPACE_URL, f"{child_id}:{role}"))
                    self.store.create_handoff(
                        child_id,
                        self.pm_id,
                        target,
                        question,
                        step["criteria"],
                        child["version"],
                        handoff_id=identity,
                    )
                    record["handoff_id"] = identity
                data["steps"][step["id"]] = record
                self.store._event(task_id, "plan.dispatched", record, parent["version"])
            states = [step["status"] for step in data["steps"].values()]
            if any(s in {"failed", "blocked"} for s in states):
                data["phase"] = "blocked"
                self.store.transition_task(task_id, "BLOCKED", parent["version"])
            elif len(states) == len(data["spec"]["steps"]) and all(
                s == "completed" for s in states
            ):
                data["phase"] = "completed"
                self.store.transition_task(task_id, "COMPLETED", parent["version"])
            self._save(task_id, data)
            return data
