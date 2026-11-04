"""Collect bounded, provenance-linked context for meeting fact checks.

Only durable task and handoff status records become verified facts. Repository
graph excerpts and approved historical lessons remain guidance, never facts
about the current meeting.
"""

from __future__ import annotations

import json
import re

_MAX_FACTS = 40
_MAX_UNTRUSTED = 8
_MAX_TEXT = 400
_SHA = re.compile(r"^[0-9a-f]{40,64}$")


def _warning(warnings: list[str], value) -> None:
    if isinstance(value, str) and value.strip():
        warnings.append(value.strip()[:300])


def _scope_source(source, *, room_id, channel_id, project_id):
    return (
        isinstance(source, dict)
        and source.get("room_id") == room_id
        and source.get("channel_id") == channel_id
        and source.get("project_id") == project_id
    )


def _current_facts(store, meeting, project_id):
    source = meeting.get("source") or {}
    room_id, channel_id = source.get("room_id"), meeting.get("channel_id")
    if not all(isinstance(value, str) and value for value in (room_id, channel_id, project_id)):
        return [], "Verified task context is unavailable: meeting scope is incomplete."

    facts = []
    with store._lock:
        rows = store.connection.execute(
            "SELECT * FROM tasks ORDER BY created_at DESC,rowid DESC LIMIT 1000"
        ).fetchall()
        for raw in rows:
            task = store._row(raw)
            task_source = task.get("source") or {}
            if not _scope_source(
                task_source,
                room_id=room_id,
                channel_id=channel_id,
                project_id=project_id,
            ):
                continue
            task_id = task.get("id")
            version = task.get("version")
            status = task.get("status")
            if (
                not isinstance(task_id, str)
                or type(version) is not int
                or not isinstance(status, str)
            ):
                continue
            facts.append(
                {
                    "statement": f"Task {task_id} status is {status} at version {version}.",
                    "verification": "verified",
                    "source_ref": f"task:{task_id}:version:{version}",
                }
            )
            handoffs = store.connection.execute(
                "SELECT id,recipient,status,version FROM handoffs WHERE task_id=? "
                "ORDER BY rowid DESC LIMIT 10",
                (task_id,),
            ).fetchall()
            for handoff in handoffs:
                if len(facts) >= _MAX_FACTS:
                    break
                facts.append(
                    {
                        "statement": (
                            f"Assignment {handoff['id']} to {handoff['recipient']} status is "
                            f"{handoff['status']} at version {handoff['version']}"
                        ),
                        "verification": "verified",
                        "source_ref": f"handoff:{handoff['id']}:task:{task_id}",
                    }
                )
            if len(facts) >= _MAX_FACTS:
                break
    warning = (
        "Verified task context is bounded; older same-scope records may be omitted."
        if len(rows) >= 1000
        else None
    )
    return facts[:_MAX_FACTS], warning


def _public_lesson(lesson):
    if not isinstance(lesson, dict):
        return None
    lesson_id = lesson.get("id")
    if not isinstance(lesson_id, str) or not re.fullmatch(r"lesson-[0-9a-f]{24}", lesson_id):
        return None
    result = {
        "kind": "approved_historical_lesson",
        "trust": "untrusted_guidance_verify_against_current_meeting_and_task",
        "lesson_id": lesson_id,
    }
    for key in ("summary", "failure_signature", "correction"):
        value = lesson.get(key)
        if isinstance(value, str) and value.strip():
            result[key] = value[:_MAX_TEXT]
    if lesson.get("root_cause_status") == "verified":
        root_cause = lesson.get("root_cause")
        if isinstance(root_cause, str) and root_cause.strip():
            result["verified_historical_root_cause"] = root_cause[:_MAX_TEXT]
            evidence = lesson.get("evidence")
            if isinstance(evidence, dict):
                refs = {
                    key: evidence[key]
                    for key in ("task_id", "final_revision", "qa_handoff_id", "reviewer_handoff_id")
                    if isinstance(evidence.get(key), str)
                }
                result["verification_refs"] = refs
    return result


async def gather_meeting_context(
    store,
    meeting,
    *,
    company_brain=None,
    lessons=None,
    actor_id=None,
    project_id=None,
):
    """Return verified scoped status facts, untrusted guidance, and coverage warnings."""
    if not isinstance(meeting, dict):
        raise ValueError("Persisted meeting record is required")
    source = meeting.get("source") or {}
    persisted_project = source.get("project_id") if isinstance(source, dict) else None
    if project_id is not None and persisted_project != project_id:
        return {
            "verified_facts": [],
            "untrusted_evidence": [],
            "warnings": ["Meeting project does not match the configured project scope."],
        }
    project_id = persisted_project
    warnings = []
    facts, scope_warning = _current_facts(store, meeting, project_id)
    _warning(warnings, scope_warning)
    if not facts:
        _warning(
            warnings,
            "No matching durable task status records were available for this meeting scope.",
        )

    untrusted = []
    if lessons is not None and actor_id and project_id:
        try:
            result = await lessons.retrieve_lessons(
                "meeting follow-up task status decisions blockers",
                actor_id=actor_id,
                project_id=project_id,
                limit=5,
            )
            for lesson in result.get("lessons", [])[:_MAX_UNTRUSTED]:
                item = _public_lesson(lesson)
                if item:
                    untrusted.append(item)
            for warning in result.get("warnings", [])[:3]:
                _warning(warnings, warning)
            if result.get("status") == "omitted_sensitive_query":
                _warning(warnings, "Approved lesson retrieval omitted a sensitive query.")
        except Exception:
            _warning(
                warnings,
                "Approved historical lesson retrieval failed; no lesson guidance was used.",
            )
    elif lessons is not None:
        _warning(
            warnings,
            "Historical lesson context is unavailable without a configured actor and project.",
        )

    if company_brain is not None:
        try:
            brain_result = await company_brain.get_context(
                "Verified implementation and QA evidence for current project task follow-up", 5
            )
            _warning(warnings, getattr(brain_result, "warning", None))
            context = getattr(brain_result, "context", None)
            if context:
                rendered = json.dumps(context, ensure_ascii=False, default=str)
                if len(rendered) > 8000:
                    rendered = rendered[:8000]
                    _warning(warnings, "Company Brain evidence was truncated for meeting context.")
                untrusted.append(
                    {
                        "kind": "company_brain_repository_evidence",
                        "trust": "untrusted_source_excerpt_not_a_verified_meeting_fact",
                        "graph_revision": context.get("revision")
                        if isinstance(context, dict)
                        else None,
                        "content": rendered,
                    }
                )
        except Exception:
            _warning(
                warnings, "Company Brain retrieval failed; no repository graph evidence was used."
            )
    else:
        _warning(
            warnings, "Company Brain is not configured; repository graph coverage is unavailable."
        )

    return {
        "verified_facts": facts[:_MAX_FACTS],
        "untrusted_evidence": untrusted[:_MAX_UNTRUSTED],
        "warnings": list(dict.fromkeys(warnings))[:10],
    }
