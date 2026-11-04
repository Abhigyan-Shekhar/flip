"""Read-only, bounded operator projection; no credentials or raw model content."""

from __future__ import annotations

import json

from .crew import _clip


def snapshot(store, settings, *, limit: int = 40) -> dict:
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("Dashboard limit must be between one and one hundred")
    with store._lock:
        rows = store.connection.execute(
            "SELECT id,objective,status,version,created_at FROM tasks "
            "WHERE json_extract(source,'$.command_receipt') IS NULL "
            "ORDER BY created_at DESC,rowid DESC LIMIT ?",
            (limit,),
        ).fetchall()
        has_plan_table = store.connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name='plans' AND type='table'"
        ).fetchone()
        tasks = []
        for row in rows:
            task = {
                "id": row["id"],
                "objective": _clip(row["objective"], 160),
                "status": row["status"],
                "version": row["version"],
                "created_at": row["created_at"],
            }
            plan = (
                store.connection.execute(
                    "SELECT data FROM plans WHERE task_id=?", (row["id"],)
                ).fetchone()
                if has_plan_table
                else None
            )
            if plan:
                value = json.loads(plan[0])
                progress = value.get("steps", {})
                task["plan"] = {
                    "phase": _clip(value.get("phase"), 50),
                    "steps": [
                        {
                            "id": str(step.get("id", ""))[:80],
                            "role": _clip(step.get("role"), 60),
                            "depends_on": list(step.get("depends_on", []))[:12],
                            "status": _clip(progress.get(step.get("id"), {}).get("status"), 50)
                            or "pending",
                        }
                        for step in value.get("spec", {}).get("steps", [])[:12]
                        if isinstance(step, dict)
                    ],
                }
            tasks.append(task)
        events = [
            {
                "task_id": row["task_id"],
                "type": _clip(row["type"], 80),
                "version": row["version"],
                "created_at": row["created_at"],
            }
            for row in store.connection.execute(
                "SELECT task_id,type,version,created_at FROM events ORDER BY id DESC LIMIT 100"
            )
        ]
        approvals = []
        has_approval_table = store.connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name='approval_workflows' AND type='table'"
        ).fetchone()
        if has_approval_table:
            for row in store.connection.execute(
                "SELECT task_id,data FROM approval_workflows LIMIT 100"
            ):
                workflow = json.loads(row["data"])
                if workflow.get("phase") in {"completed", "cancelled"}:
                    continue
                approvals.append(
                    {
                        "task_id": row["task_id"],
                        "phase": _clip(workflow.get("phase"), 60),
                        "engineering_approved": bool(
                            workflow.get("engineering_approval", {}).get("approved")
                        ),
                        "release_approved": bool(
                            workflow.get("release_approval", {}).get("approved")
                        ),
                    }
                )
    agents = []
    for role, agent_id in (
        ("pm", settings.pm_agent_id),
        ("research", settings.research_agent_id),
        ("engineering", settings.engineering_agent_id),
        ("qa", settings.qa_agent_id),
        ("reviewer", settings.reviewer_agent_id),
        ("release", settings.release_agent_id),
        *((role, spec.get("agent_id", "")) for role, spec in settings.specialist_targets.items()),
    ):
        if agent_id:
            agents.append({"role": role, "id": _clip(agent_id, 100), "live": "not_probed"})
    failed = [t for t in tasks if t["status"] in {"BLOCKED", "FAILED", "NEEDS_CLARIFICATION"}]
    return {
        "version": 1,
        "summary": {
            "listed_tasks": len(tasks),
            "in_progress": sum(t["status"] == "IN_PROGRESS" for t in tasks),
            "failures": len(failed),
            "pending_workflows": len(approvals),
        },
        "tasks": tasks,
        "agents": agents,
        "approvals": approvals,
        "activity": events,
        "failures": failed,
        "notes": [
            "Agent registration is configured; real presence has not been probed.",
            "This snapshot is read-only and omits raw prompts, source bodies and credentials.",
        ],
    }
