"""Bounded execution receipts from durable records; private payloads stay private."""

import re
import sqlite3
from urllib.parse import urlsplit

from .crew import _clip, _handoff_identifier

_FIELDS = frozenset(
    {
        "status",
        "role",
        "assignment_id",
        "implementation_assignment",
        "handoff_id",
        "task_id",
        "session_id",
        "tested_revision",
        "reviewed_revision",
        "final_revision",
        "base_revision",
        "attempt",
        "approved",
        "actor",
        "reason",
        "severity",
        "lesson_id",
        "source_uid",
        "pr_url",
        "url",
        "used",
        "provider",
        "verified",
        "outcome",
        "exit_code",
        "timed_out",
        "diff_sha256",
    }
)


def safe_receipt(value, *, depth=0):
    if depth > 5:
        return None
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if key in _FIELDS:
                if key.endswith("revision"):
                    result[key] = (
                        item
                        if isinstance(item, str) and re.fullmatch(r"[a-f0-9]{40,64}", item)
                        else "unknown"
                    )
                elif key in {"url", "pr_url"}:
                    parsed = urlsplit(item) if isinstance(item, str) else None
                    if (
                        parsed
                        and parsed.scheme == "https"
                        and parsed.hostname == "github.com"
                        and not (parsed.query or parsed.fragment or parsed.username)
                    ):
                        result[key] = item[:400]
                elif isinstance(item, str):
                    result[key] = (
                        _handoff_identifier(item) if key == "handoff_id" else _clip(item, 200)
                    )
                elif type(item) in {bool, int, float} or item is None:
                    result[key] = item
            elif key in {
                "commands",
                "findings",
                "retry_history",
                "approvals",
                "result",
                "qa",
                "review",
                "evidence",
                "lessons",
                "documentation",
            }:
                cleaned = safe_receipt(item, depth=depth + 1)
                if cleaned:
                    result[key] = cleaned
        return result
    if isinstance(value, list):
        return [cleaned for item in value[:30] if (cleaned := safe_receipt(item, depth=depth + 1))]
    return None


def execution_trace(store, task_id, *, max_children=30):
    task = store.get_task(task_id)
    if task is None:
        raise KeyError(task_id)
    with store._lock:
        tasks = [task]
        # Bounded breadth-first task lineage, never scan private source artifacts.
        for parent in tasks:
            if len(tasks) >= max_children + 1:
                break
            rows = store.connection.execute(
                "SELECT * FROM tasks WHERE (json_extract(source,'$.parent_task_id')=? "
                "OR json_extract(source,'$.evidence_task_id')=? "
                "OR json_extract(source,'$.source_evidence_task_id')=?) "
                "ORDER BY created_at,id LIMIT ?",
                (parent["id"], parent["id"], parent["id"], max_children + 1 - len(tasks)),
            ).fetchall()
            tasks.extend(store._row(row) for row in rows)
        records = []
        for item in tasks:
            handoffs = [
                store._row(row)
                for row in store.connection.execute(
                    "SELECT * FROM handoffs WHERE task_id=? ORDER BY rowid LIMIT 100", (item["id"],)
                )
            ]
            events = store.list_events(item["id"])[-250:]
            source = item["source"]
            records.append(
                {
                    "task_id": item["id"],
                    "status": item["status"],
                    "version": item["version"],
                    "origin": {
                        key: _clip(source[key], 120)
                        for key in (
                            "room_id",
                            "channel_id",
                            "thread_id",
                            "message_id",
                            "meeting_id",
                            "user_id",
                            "parent_task_id",
                        )
                        if isinstance(source.get(key), str)
                    },
                    "assignments": [
                        {
                            "handoff_id": _handoff_identifier(h["id"]),
                            "sender": _clip(h["sender"], 120),
                            "recipient": _clip(h["recipient"], 120),
                            "status": h["status"],
                            "result": safe_receipt(h.get("result")),
                        }
                        for h in handoffs
                    ],
                    "events": [
                        {
                            "id": e["id"],
                            "type": _clip(e["type"], 100),
                            "at": e["created_at"],
                            "details": safe_receipt(e["details"]),
                        }
                        for e in events
                    ],
                }
            )
        try:
            row = store.connection.execute(
                "SELECT data FROM plans WHERE task_id=?", (task_id,)
            ).fetchone()
        except sqlite3.OperationalError:
            row = None
        plan = None
        if row:
            import json

            raw = json.loads(row[0])
            plan = {
                "phase": raw.get("phase"),
                "steps": [
                    {
                        "id": _clip(s["id"], 80),
                        "role": _clip(s["role"], 80),
                        "depends_on": [_clip(dep, 80) for dep in s.get("depends_on", [])],
                        "task_id": raw.get("steps", {}).get(s["id"], {}).get("task_id"),
                    }
                    for s in raw.get("spec", {}).get("steps", [])[:12]
                ],
            }
    return {
        "task_id": task_id,
        "plan": plan,
        "tasks": records,
        "bounded": True,
        "private_payloads_omitted": True,
        "limitations": "Durable recorded evidence; absent facts remain unknown. "
        "No raw transcripts, memory, command output or diffs.",
    }
