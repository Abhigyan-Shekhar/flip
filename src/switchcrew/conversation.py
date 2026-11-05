"""Explicit human requests and decisions without a JSON interface."""

import re
from uuid import UUID

_DECISION = re.compile(
    r"(?:I\s+)?(approve|reject)\s+(engineering|release)\s+for\s+task\s+"
    r"([0-9a-f-]{36})\s+version\s+([1-9][0-9]{0,8})\.?",
    re.I,
)
_DRAFT_DECISION = re.compile(
    r"(?:I\s+)?(approve|reject)\s+draft\s+for\s+task\s+([0-9a-f-]{36})"
    r"\s+step\s+(step-[1-9][0-9]?)\s+version\s+([1-9][0-9]{0,8})\.?",
    re.I,
)


def human_request(text, engineering_job_key=""):
    if text.lstrip().startswith(("{", "[")):
        return None
    draft = _DRAFT_DECISION.fullmatch(text.strip())
    if draft:
        try:
            task_id = str(UUID(draft[2]))
        except ValueError:
            return None
        return {
            "type": "plan_review",
            "task_id": task_id,
            "step_id": draft[3].lower(),
            "approved": draft[1].lower() == "approve",
            "expected_version": int(draft[4]),
        }
    decision = _DECISION.fullmatch(text.strip())
    if decision:
        try:
            task_id = str(UUID(decision[3]))
        except ValueError:
            return None
        return {
            "type": "approval",
            "task_id": task_id,
            "stage": decision[2].lower(),
            "approved": decision[1].lower() == "approve",
            "expected_version": int(decision[4]),
        }
    # Ordinary prose must still state what success means. Never invent criteria
    # or interpret casual agreement as authorization.
    parts = re.split(
        r"\n\s*(?:Acceptance criteria|Success criteria|Done when):\s*\n?",
        text,
        maxsplit=1,
        flags=re.I,
    )
    if len(parts) != 2 or not parts[0].strip() or len(parts[0]) > 10000:
        return None
    criteria = [
        re.sub(r"^(?:[-*]|\d+[.)])\s+", "", line.strip())
        for line in parts[1].splitlines()
        if line.strip()
    ]
    if not 1 <= len(criteria) <= 10 or any(len(c) > 2000 for c in criteria):
        return None
    question = parts[0].strip()
    coding = bool(
        re.match(r"(?:Please\s+)?(?:fix|build|implement|change|add|update)\b", question, re.I)
    )
    coordination = bool(
        re.match(r"(?:Hey\s+)?(?:team|PM)\b", question, re.I)
        and re.search(r"\b(coordinate|delegate|orchestrate)\b", question, re.I)
    )
    return {
        "question": question,
        "acceptance_criteria": criteria,
        "kind": "orchestrate" if coordination else ("engineering" if coding else "research"),
        **({"job_key": engineering_job_key} if coding else {}),
    }


def decision_phrase(task_id, stage, version, *, approved=True):
    return f"{'Approve' if approved else 'Reject'} {stage} for task {task_id} version {version}"


_CONTROL_PHRASES = {
    "show me the logs": "logs",
    "show logs": "logs",
    "show me the sandbox logs": "logs",
    "show sandbox status": "status",
    "what is the status": "status",
    "what's the status": "status",
    "stop the current task": "cancel",
    "cancel the current task": "cancel",
    "show me the findings": "findings",
    "what did security find": "findings",
}


def conversation_control(text):
    """Recognize bounded controls; never infer approvals or shell commands."""
    if not isinstance(text, str) or len(text) > 300:
        return None
    return _CONTROL_PHRASES.get(
        re.sub(r"[.!?]+$", "", text.strip()).strip().replace("’", "'").lower()
    )
