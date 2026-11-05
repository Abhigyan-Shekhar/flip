"""Private, read-only incident room commands for SwitchCrew tasks."""

import json
import re
import sqlite3
from dataclasses import replace
from uuid import UUID

from .store import ConflictError, InvalidTransitionError

_COMMAND = re.compile(
    r"^/(?:crew|flip)\s+(status|task|team|why|replay|trace|dependencies|evidence|logs|findings|cancel)\s+([0-9a-fA-F-]{36})\s*$",
    re.I,
)
_MAX_TEXT = 12_000
_SAFE_EVENTS = {
    "task.created",
    "task.transitioned",
    "plan.dispatched",
    "handoff.created",
    "handoff.accepted",
    "handoff.completed",
    "engineering.decision",
    "release.decision",
    "approval.requested",
    "approval.decided",
}
_SECRET = re.compile(
    r"(?i)(?:bearer\s+\S+|"
    r"(?:api[_ -]?key|access[_ -]?token|refresh[_ -]?token|secret|password|"
    r"prompt|raw[_ -]?tool[_ -]?output|diff|private[_ -]?context)\s*[:=]\s*\S+|"
    r"(?:/[\w.-]+){2,})"
)
_CREW_PREFIX = re.compile(r"^/(?:crew|flip)(?:\s|$)", re.I)


def _clip(value, limit=160):
    if not isinstance(value, str):
        return ""
    value = _SECRET.sub("[redacted]", value)
    value = re.sub(
        r"\b[A-Za-z0-9_-]{32,}\b",
        lambda match: match.group(0) if _is_uuid(match.group(0)) else "[redacted]",
        value,
    )
    return value[:limit]


def _is_uuid(value):
    try:
        UUID(value)
        return True
    except (ValueError, TypeError, AttributeError):
        return False


def _handoff_identifier(value):
    if isinstance(value, str) and (
        _is_uuid(value) or re.fullmatch(r"(?:qa|review|engineering|release)-[0-9a-f]{32,64}", value)
    ):
        return value
    return "[redacted]"


def _plan(store, task_id):
    try:
        with store._lock:
            row = store.connection.execute(
                "SELECT data FROM plans WHERE task_id=?", (task_id,)
            ).fetchone()
        return json.loads(row[0]) if row else None
    except sqlite3.OperationalError:
        return None


def _task_scope(task, source):
    origin = task.get("source") or {}
    return origin.get("room_id") == source.room_id and (
        origin.get("thread_id") or origin.get("message_id")
    ) == (source.thread_id or source.message_id)


def _employee(store, task_id):
    try:
        row = store.connection.execute(
            "SELECT employee_id FROM employee_assignments WHERE task_id=? AND status='active'",
            (task_id,),
        ).fetchone()
        return row[0] if row else None
    except sqlite3.OperationalError:
        return None


def _employee_for_actor(store, actor_id):
    try:
        row = store.connection.execute(
            "SELECT employee_id FROM employee_registry WHERE actor_id=? AND enabled=1", (actor_id,)
        ).fetchone()
        return row[0] if row else None
    except sqlite3.OperationalError:
        return None


def _render(store, task, kind):
    task_id = task["id"]
    plan = _plan(store, task_id)
    lines = []
    if kind == "crew_status":
        lines = [f"Task {task_id}: {task['status']} (version {task['version']})"]
        if plan:
            lines.append(f"Workflow: {_clip(plan.get('phase'), 80) or 'unknown'}")
        try:
            from .approval import ApprovalWorkflow

            approval = ApprovalWorkflow(store).get(task_id)
        except sqlite3.Error:
            approval = None
        if approval:
            phase = _clip(approval.get("phase"), 80) or "unknown"
            raw_attempt = approval.get("attempt", 0)
            attempt = raw_attempt if type(raw_attempt) is int and 0 <= raw_attempt <= 100 else 0
            lines.append(f"Approval phase: {phase}; attempt {attempt}")
    elif kind == "crew_team":
        if not plan or not isinstance(plan.get("team"), list):
            lines = ["No incident team plan is recorded for this task."]
        else:
            lines = ["Team:"]
            steps = plan.get("steps") if isinstance(plan.get("steps"), dict) else {}
            step_roles = {}
            for step in (
                plan.get("spec", {}).get("steps", []) if isinstance(plan.get("spec"), dict) else []
            ):
                if isinstance(step, dict):
                    step_roles[step.get("id")] = step.get("role")
            for role in plan["team"][:20]:
                if not isinstance(role, str):
                    continue
                target_id = (
                    (plan.get("targets") or {}).get(role, "")
                    if isinstance(plan.get("targets"), dict)
                    else ""
                )
                matched = [
                    (sid, v)
                    for sid, v in steps.items()
                    if isinstance(v, dict)
                    and (v.get("role") == role or step_roles.get(sid) == role)
                ]
                spec_ids = [
                    sid for sid, assigned_role in step_roles.items() if assigned_role == role
                ]
                identity = (
                    f" (agent {_clip(target_id, 80)})"
                    if isinstance(target_id, str) and target_id
                    else ""
                )
                if matched:
                    for sid, assignment in matched[:10]:
                        state = _clip(assignment.get("status"), 40) or "pending"
                        lines.append(f"- {_clip(role, 60)}{identity} [{_clip(sid, 64)}]: {state}")
                elif spec_ids:
                    for sid in spec_ids[:10]:
                        lines.append(f"- {_clip(role, 60)}{identity} [{_clip(sid, 64)}]: pending")
                else:
                    lines.append(f"- {_clip(role, 60)}{identity}: pending")
    elif kind == "crew_why":
        reasons = plan.get("selection_reasons") if plan else None
        if not isinstance(reasons, dict) or not reasons:
            lines = ["No recorded team selection reasons are available."]
        else:
            lines = ["Recorded team selection reasons:"]
            for role, details in list(reasons.items())[:20]:
                if not isinstance(details, dict):
                    continue
                reason = _clip(details.get("reason"), 300)
                caps = details.get("capabilities", [])
                caps = (
                    ", ".join(_clip(x, 60) for x in caps[:10] if isinstance(x, str))
                    if isinstance(caps, list)
                    else ""
                )
                lines.append(
                    f"- {_clip(role, 60)}: {reason}" + (f" (capabilities: {caps})" if caps else "")
                )
    else:
        lines = ["Recorded activity:"]
        with store._lock:
            events = [
                store._row(row)
                for row in store.connection.execute(
                    "SELECT * FROM events WHERE task_id=? ORDER BY id LIMIT 1000", (task_id,)
                )
            ]
        for event in events:
            etype = event.get("type")
            if etype not in _SAFE_EVENTS:
                continue
            d = event.get("details") if isinstance(event.get("details"), dict) else {}
            parts = [f"{_clip(etype, 50)} at {_clip(event.get('created_at'), 40)}"]
            if etype == "task.transitioned":
                parts.append(f"{_clip(d.get('from'), 30)} → {_clip(d.get('to'), 30)}")
            if etype.startswith("handoff.") or etype == "handoff.created":
                hid = _handoff_identifier(d.get("handoff_id"))
                if hid:
                    parts.append(f"handoff {hid}")
                for key in ("sender", "recipient"):
                    val = _clip(d.get(key), 80)
                    if val:
                        parts.append(f"{key} {val}")
                if etype != "handoff.created":
                    parts.append(etype.split(".", 1)[1])
            if etype.endswith(".decision"):
                actor, decided = _clip(d.get("actor"), 100), _clip(d.get("decided_at"), 40)
                if actor:
                    parts.append(f"approval by {actor}")
                if decided:
                    parts.append(f"at {decided}")
                if type(d.get("approved")) is bool:
                    parts.append("approved" if d["approved"] else "rejected")
            lines.append("- " + "; ".join(parts))
    text = "\n".join(lines)
    return text[:_MAX_TEXT]


def _display(value, limit=800):
    from .security import _redact

    return _clip(_redact(value, limit), limit).replace("@", "＠").replace("`", "ˋ")


def _inspection(store, task, action):
    from .trace import execution_trace

    lines = [f"FLIP SANDBOX · TASK {task['id']}", f"Status: {task['status']}"]
    found = False
    for child in execution_trace(store, task["id"])["tasks"]:
        with store._lock:
            rows = store.connection.execute(
                "SELECT * FROM handoffs WHERE task_id=? ORDER BY rowid DESC LIMIT 100",
                (child["task_id"],),
            ).fetchall()
        for row in rows:
            handoff = store._row(row)
            result = handoff.get("result")
            if not isinstance(result, dict):
                continue
            revision = result.get("tested_revision", result.get("revision", ""))
            if not isinstance(revision, str) or not re.fullmatch(r"[a-f0-9]{40,64}", revision):
                revision = "unknown"
            if action == "findings":
                report = result.get("security_report", result)
                if not isinstance(report, dict) or not isinstance(
                    report.get("findings"), (list, tuple)
                ):
                    continue
                found = True
                lines.append(
                    f"Security: {_display(report.get('status', 'unknown'), 80)}; "
                    f"revision {revision}"
                )
                for index, finding in enumerate(report["findings"][:30], 1):
                    if isinstance(finding, dict):
                        lines.append(
                            f"{index}. {_display(finding.get('finding_id', 'unassigned'), 80)} "
                            f"[{_display(finding.get('severity', 'unknown'), 30)}] "
                            f"{_display(finding.get('file', ''), 180)}: "
                            f"{_display(finding.get('explanation', ''), 400)}"
                        )
                if not report["findings"]:
                    lines.append("No findings recorded; consult scanner coverage and warnings.")
                for warning in (report.get("warnings") or [])[:5]:
                    lines.append("Warning: " + _display(warning, 200))
            else:
                commands = result.get("commands")
                if not isinstance(commands, list):
                    continue
                for command in commands[-10:]:
                    if not isinstance(command, dict):
                        continue
                    found = True
                    lines.append(
                        f"Agent: {_display(handoff['recipient'], 80)}; revision {revision}"
                    )
                    argv = command.get("command", [])
                    if isinstance(argv, list) and all(isinstance(arg, str) for arg in argv):
                        lines.append("$ " + _display(json.dumps(argv, ensure_ascii=False), 350))
                    lines.append(
                        _display(command.get("output_summary", "No display output recorded."))
                    )
                    lines.append(
                        f"Exit code: {_display(command.get('exit_code', 'unknown'), 30)}; "
                        f"timed out: {command.get('timed_out') is True}"
                    )
    if not found:
        lines.append(
            "No recorded findings." if action == "findings" else "No command logs recorded yet."
        )
    if action == "logs":
        lines.append(
            "Bounded recorded previews; live streaming and full log downloads are unavailable."
        )
    return "\n".join(lines)[:_MAX_TEXT]


def _context_control(store, source):
    from .conversation import conversation_control

    action = conversation_control(source.body)
    if action is None:
        return None
    candidates = []
    with store._lock:
        rows = store.connection.execute(
            "SELECT * FROM tasks ORDER BY rowid DESC LIMIT 1001"
        ).fetchall()
    for row in rows:
        task = store._row(row)
        origin = task.get("source") or {}
        if (
            origin.get("command_receipt")
            or origin.get("parent_task_id")
            or origin.get("security_gate")
        ):
            continue
        if not _task_scope(task, source) or origin.get("user_id") != source.user_id:
            continue
        if action == "cancel" and task["status"] in {
            "COMPLETED",
            "CANCELLED",
            "FAILED",
            "BLOCKED",
            "MERGED",
        }:
            continue
        candidates.append(task)
    if len(candidates) == 2:
        # A recorded investigation→proposal relationship is explicit context,
        # unlike choosing whichever unrelated task happened to be newest.
        children = [
            t
            for t in candidates
            if t["source"].get("investigation_task_id")
            in {other["id"] for other in candidates if other["id"] != t["id"]}
        ]
        if len(children) == 1:
            candidates = children
    if len(candidates) != 1 or len(rows) > 1000:
        return {
            "handled": True,
            "task_id": None,
            "kind": "crew_context",
            "durable_reply": True,
            "text": "Select a task explicitly: /flip "
            + action
            + " TASK_UUID. "
            + (
                "Multiple tasks match this thread."
                if candidates
                else "No task matches this thread, or you are not authorized for its context."
            ),
        }
    return replace(source, body=f"/flip {action} {candidates[0]['id']}")


def handle_crew_command(store, source, *, can_cancel: bool = False) -> dict | None:
    """Handle a text command from an already authenticated DiscordSource."""
    contextual = _context_control(store, source)
    if isinstance(contextual, dict):
        return contextual
    if contextual is not None:
        source = contextual
    text = source.body.strip()
    if re.fullmatch(r"/(?:crew|flip)\s+help", text, re.I):
        return {
            "handled": True,
            "task_id": None,
            "kind": "crew_help",
            "durable_reply": True,
            "text": "Use /flip tasks or /flip status, logs, findings, team, trace, evidence, "
            "dependencies, "
            "or cancel TASK_UUID. Commands stay scoped to the original task thread. "
            "Logs are bounded recorded previews. Approvals require the exact versioned phrase.",
        }
    text = re.sub(r"^/flip(?=\s|$)", "/crew", text, flags=re.I)
    if re.match(r"^/crew\s+create(?:\s|$)", text, re.I):
        parts = [
            part.strip() for part in re.sub(r"^/crew\s+create\s*", "", text, flags=re.I).split("|")
        ]
        if (
            len(parts) < 2
            or not all(parts)
            or len(parts) > 13
            or len(parts[0]) > 10000
            or any(len(p) > 2000 for p in parts[1:])
        ):
            return {
                "handled": True,
                "task_id": None,
                "kind": "crew_invalid",
                "text": "Use /crew create objective | acceptance criterion | another criterion.",
            }
        return {
            "handled": True,
            "task_id": None,
            "kind": "crew_create",
            "request": {"question": parts[0], "acceptance_criteria": parts[1:]},
        }
    listing = re.fullmatch(
        r"/crew\s+(tasks|mine|completed)(?:\s+(active|blocked|completed|all|employee\s+[\w.-]{1,100}))?",
        text,
        re.I,
    )
    if listing:
        action, selected = listing.group(1).lower(), (listing.group(2) or "active").lower()
        rows = []
        with store._lock:
            for row in store.connection.execute(
                "SELECT * FROM tasks ORDER BY created_at DESC,rowid DESC LIMIT 1000"
            ):
                task = store._row(row)
                origin = task["source"]
                assigned = _employee(store, task["id"]) or origin.get("assigned_employee")
                same_channel = (
                    origin.get("room_id") == source.room_id
                    and origin.get("guild_id") == source.guild_id
                    and origin.get("channel_id") == source.channel_id
                )
                if origin.get("command_receipt") or not (same_channel or _task_scope(task, source)):
                    continue
                if action == "mine" and source.user_id != origin.get("user_id"):
                    if not assigned or assigned not in {
                        source.user_id,
                        _employee_for_actor(store, source.user_id),
                    }:
                        continue
                if selected.startswith("employee ") and assigned != selected.split(" ", 1)[1]:
                    continue
                state = task["status"]
                if (action == "completed" or selected == "completed") and state != "COMPLETED":
                    continue
                if selected == "blocked" and state not in {
                    "BLOCKED",
                    "FAILED",
                    "NEEDS_CLARIFICATION",
                }:
                    continue
                if (
                    action != "completed"
                    and selected == "active"
                    and state in {"COMPLETED", "CANCELLED", "BLOCKED", "FAILED"}
                ):
                    continue
                rows.append(
                    f"- {task['id']}: {state}; thread "
                    + _clip(origin.get("thread_id") or origin.get("message_id"), 100)
                )
                if len(rows) >= 30:
                    break
        return {
            "handled": True,
            "task_id": None,
            "kind": "crew_list",
            "durable_reply": True,
            "text": "Tasks in this channel (up to 30):\n"
            + ("\n".join(rows) or "No matching tasks."),
        }
    match = _COMMAND.fullmatch(source.body)
    if not match:
        if _CREW_PREFIX.match(source.body):
            return {
                "handled": True,
                "task_id": None,
                "kind": "crew_invalid",
                "text": "Use /crew status, team, why, replay, or cancel followed by a task UUID.",
            }
        return None
    action, task_id = match.group(1).lower(), match.group(2).lower()
    try:
        task_id = str(UUID(task_id))
    except ValueError:
        return {
            "handled": True,
            "task_id": None,
            "kind": "crew_invalid",
            "text": "Use /crew status, team, why, replay, or cancel followed by a task UUID.",
        }
    action = "status" if action == "task" else action
    kind = f"crew_{action}"
    denied = {
        "handled": True,
        "task_id": None,
        "kind": kind,
        "text": "This command is unavailable in this room or thread.",
    }
    task = store.get_task(task_id)
    if not task or not _task_scope(task, source):
        return denied
    if action in {"logs", "findings"}:
        return {
            "handled": True,
            "task_id": task_id,
            "kind": kind,
            "text": _inspection(store, task, action),
        }
    if action in {"trace", "evidence", "dependencies"}:
        from .trace import execution_trace

        trace = execution_trace(store, task_id)
        value = trace.get("plan") if action == "dependencies" else trace
        return {
            "handled": True,
            "task_id": task_id,
            "kind": kind,
            "text": json.dumps(value, ensure_ascii=False)[:_MAX_TEXT],
        }
    if action == "cancel":
        owner = (task.get("source") or {}).get("user_id") == source.user_id
        if not owner and not can_cancel:
            return {**denied, "text": "You are not authorized to cancel this task."}
        if task["status"] == "CANCELLED":
            text = "Task is already cancelled."
        else:
            try:
                cancelled = store.transition_task(
                    task_id,
                    "CANCELLED",
                    task["version"],
                    {"actor": source.user_id, "reason": "crew_command"},
                )
                text = f"Task {task_id} cancelled (version {cancelled['version']})."
            except (InvalidTransitionError, ConflictError):
                latest = store.get_task(task_id)
                text = (
                    "Task is already cancelled."
                    if latest and latest["status"] == "CANCELLED"
                    else "Task cannot be cancelled in its current state."
                )
        return {"handled": True, "task_id": task_id, "kind": kind, "text": text}
    return {"handled": True, "task_id": task_id, "kind": kind, "text": _render(store, task, kind)}
