"""Evidence-reference envelopes for Switch transport; durable payloads remain in SQLite."""

import hashlib
import json
import re

from .store import encode

_REFERENCE = re.compile(r"\n\nReference: `([0-9a-f-]{36})`\s*$")


def read_message(body, store):
    """Resolve prose against durable intent; visible wording grants no authority.

    Legacy JSON is retained for history and rolling upgrades. Authentication,
    sender/recipient, room, task and result checks remain at each receiver.
    """
    reference = _REFERENCE.search(body)
    if reference:
        item = store.get_outbox(reference[1])
        if not item or item["payload"].get("type") not in {
            "handoff",
            "handoff.accepted",
            "handoff.completed",
        }:
            raise ValueError("Unknown durable communication reference")
        return {
            **transport_payload(item["payload"]),
            "task_id": item["task_id"],
            "delivery_id": item["id"],
        }
    marker = re.search(r"```switchcrew\n(.*?)\n```\s*$", body, re.DOTALL)
    value = json.loads(marker.group(1) if marker else body)
    if not isinstance(value, dict):
        raise ValueError("Expected a handoff message")
    return value


def _text(value, limit=600):
    # Prevent user-controlled text from creating platform mentions or a second
    # audit footer. The addressed Switch target is supplied separately.
    return str(value).replace("@", "@\u200b").replace("`", "'")[:limit]


def research_summary(result):
    lines = ["Research result:"]
    artifact = result.get("summary") or result.get("artifact")
    if isinstance(artifact, str) and artifact.strip():
        lines.append(_text(artifact, 650))
    for source in result.get("evidence", [])[:3]:
        if isinstance(source, dict):
            lines.extend(_text(excerpt, 700) for excerpt in source.get("excerpts", [])[:2])
            lines.append(_text(source.get("url", ""), 300))
        elif isinstance(source, str):
            lines.append(_text(source, 220))
    if len(lines) == 1:
        lines.append("I couldn't find supporting evidence in the configured sources.")
    limits = result.get("limitations")
    if isinstance(limits, list):
        lines.append("Limitations:\n" + "\n".join("- " + _text(item, 180) for item in limits[:2]))
    elif isinstance(limits, str) and limits:
        lines.append(_text(limits, 400))
    return "\n\n".join(lines)


def plan_summary(data):
    text = "PM orchestration " + _text(data["phase"], 40) + "."
    for step in list(data.get("steps", {}).values())[:20]:
        role = _text(step.get("role", "Specialist"), 80)
        status = _text(step.get("status", "unknown"), 80).replace("_", " ")
        text += f"\n- {role}: {status}."
    return text


def artifact_preview(result, role):
    """Summarize real specialist output without serializing its data structure."""
    if role == "analytics" and isinstance(result.get("metrics"), dict):
        metrics = result["metrics"]
        counts = ", ".join(
            f"{_text(status, 40)}: {count}"
            for status, count in metrics.get("status_counts", {}).items()
        )
        return f"Snapshot analysis: {metrics.get('record_count', 0)} records. {counts}."
    if role == "scheduling" and isinstance(result.get("proposal"), dict):
        proposal = result["proposal"]
        return (
            f"Draft: {_text(proposal.get('title', 'Meeting'), 100)}. "
            f"{len(proposal.get('conflicts', []))} conflicts in the supplied calendar snapshot."
        )
    summary = result.get("summary")
    if isinstance(summary, str):
        return _text(summary, 700)
    artifact = result.get("artifact")
    if isinstance(artifact, dict) and isinstance(artifact.get("content"), str):
        content = re.sub(r"```json\n.*?\n```", "", artifact["content"], flags=re.S)
        return _text(content, 700)
    return ""


def render_message(item, task, handoff, *, role="", target=""):
    """Readable Switch/Discord messages with an opaque durable audit reference."""
    payload = item["payload"]
    kind = payload["type"]
    result = payload.get("result") or {}
    status = result.get("status", "") if isinstance(result, dict) else ""
    if kind == "handoff":
        if re.search(r"\bqa\b", target, re.I):
            request = "Please run the independent QA checks on the committed revision."
        elif re.search(r"\breviewer\b", target, re.I):
            request = "Please independently review the committed revision and its QA evidence."
        elif re.search(r"\brelease\b", target, re.I):
            request = "Please verify human approvals and release gates before creating a draft PR."
        elif handoff["id"].startswith("engineering-"):
            request = "Please fix the reported issues and commit a correction for fresh checks."
        else:
            request = "Please take the next step on this task."
        text = request + "\n\n" + _text(task["objective"])
        criteria = handoff.get("acceptance_criteria", [])
        if criteria:
            text += "\n\nWhat needs to pass:\n" + "\n".join(
                "- " + _text(criterion, 180) for criterion in criteria[:4]
            )
    elif kind == "handoff.accepted":
        text = "I've accepted the assignment. I'll check the requirements and report back here."
    elif status == "implemented":
        text = "I've committed the implementation. QA can now check the exact revision."
    elif status == "bug_reproduced":
        text = "Bug reproduced: incomplete native Trivy output was accepted as clean."
    elif status == "not_reproduced":
        text = "The configured reproduction did not confirm the suspected defect."
    elif status == "qa_failed":
        text = "The tests failed. I'm sending the findings back to Engineering for correction."
        commands = result.get("commands", [])
        failed = sum(c.get("exit_code") != 0 or bool(c.get("timed_out")) for c in commands)
        if commands:
            text += f" {failed} of {len(commands)} configured checks failed."
    elif status == "review_accepted":
        text = (
            "I've completed the independent review and found no issues under the configured checks."
        )
    elif status == "review_rejected":
        text = "The review found issues. I'm sending the findings back to Engineering."
    elif status == "verified_success":
        text = "The QA tests passed." if role == "qa" else "The configured checks passed."
        if role == "qa":
            text += " I'm sending the tested revision to the independent Reviewer."
    elif status == "completed":
        text = "I've completed the assignment. The verified result is ready for PM."
        preview = artifact_preview(result, role)
        if preview:
            text += "\n\n" + preview
    elif status == "evidence_found":
        text = "I've finished the investigation and sent the supporting evidence to PM."
    elif status == "findings":
        text = "The security scan found issues that need review before release."
    elif status == "scanner_failed":
        text = "The scanner failed; the Security gate remains blocked."
    elif status in {"ready", "delegation_ready"}:
        text = "I've prepared the next step and sent the details to PM."
    elif status in {"stale_qa", "stale_review"}:
        text = "The revision changed. Fresh checks are needed before this can proceed."
    elif status == "awaiting_approval":
        text = "I'm waiting for explicit human approval before starting."
    elif status == "pending_execution":
        text = "I'm waiting for the execution outcome to be confirmed before proceeding."
    elif status == "needs_clarification":
        text = "I need more information about the request before I can proceed."
    else:
        text = "I couldn't complete the assignment. PM can review the findings and next steps."
    if isinstance(result, dict):
        revision = (
            result.get("final_revision") or result.get("tested_revision") or result.get("revision")
        )
        if isinstance(revision, str) and re.fullmatch(r"[0-9a-f]{40}", revision):
            text += "\nRevision: " + revision[:12] + "."
    return text + "\n\nReference: `" + item["id"] + "`"


def transport_payload(payload):
    if (
        payload.get("type") not in {"handoff.accepted", "handoff.completed"}
        or "result" not in payload
    ):
        return payload
    result = payload.get("result")
    return {key: value for key, value in payload.items() if key != "result"} | {
        "result_status": result.get("status") if isinstance(result, dict) else None,
        "result_sha256": hashlib.sha256(encode(result).encode()).hexdigest(),
        "evidence_location": "durable_handoff_record",
    }


def result_matches(message, handoff):
    if message.get("type") != "handoff.completed":
        return True
    digest = message.get("result_sha256")
    return (
        digest is None
        or digest == hashlib.sha256(encode(handoff.get("result")).encode()).hexdigest()
    )
