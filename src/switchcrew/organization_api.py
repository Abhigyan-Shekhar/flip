"""Authenticated organization workflows backed by existing durable services."""

import json
import os
from typing import Annotated

from fastapi import Depends, HTTPException, Request


def _args(body, allowed, required=()):
    if (
        not isinstance(body, dict)
        or set(body) - set(allowed)
        or any(key not in body for key in required)
    ):
        raise HTTPException(422, "Invalid workflow fields")
    if len(json.dumps(body)) > 100000:
        raise HTTPException(422, "Workflow input exceeds size limit")
    for key, value in body.items():
        if key in {
            "participants",
            "regression_command",
            "applicability",
            "supersedes",
            "contradicts",
        }:
            if (
                not isinstance(value, list)
                or not (0 if key in {"supersedes", "contradicts"} else 1) <= len(value) <= 100
                or any(not isinstance(v, str) or not v.strip() or len(v) > 2000 for v in value)
            ):
                raise HTTPException(422, "Invalid workflow list")
        elif key in {"actions"}:
            if (
                not isinstance(value, list)
                or not 1 <= len(value) <= 50
                or any(not isinstance(v, dict) for v in value)
            ):
                raise HTTPException(422, "Invalid action list")
        elif key == "source":
            if not (isinstance(value, str) and 0 < len(value) <= 2000) and (
                not isinstance(value, dict) or any(not isinstance(v, str) for v in value.values())
            ):
                raise HTTPException(422, "Invalid source context")
        elif key == "disclosure_acknowledged":
            if type(value) is not bool:
                raise HTTPException(422, "Explicit disclosure acknowledgment required")
        elif key in {"limit", "expected_version"}:
            if type(value) is not int or not (0 if key == "expected_version" else 1) <= value <= (
                1000000 if key == "expected_version" else 20
            ):
                raise HTTPException(422, "Invalid result limit")
        elif value is not None and (
            not isinstance(value, str) or not value.strip() or len(value) > 10000
        ):
            raise HTTPException(422, "Invalid workflow text")
    return body


def install_organization_routes(app, settings, authenticate_human, sanitize):
    def limited_actor(request: Request, actor: Annotated[str, Depends(authenticate_human)]):
        from .limits import consume_budget

        event_id = getattr(request.state, "request_id", "")
        if not consume_budget(request.app.state.store, "human:" + actor, event_id):
            raise HTTPException(429, "Human workflow request budget exhausted")
        return actor

    human = Depends(limited_actor)

    def service(request, name):
        value = getattr(request.app.state, name, None)
        if value is None:
            raise HTTPException(409, "Organization capability is not configured")
        return value

    @app.post("/meetings")
    def create_meeting(body: dict, request: Request, actor: Annotated[str, human]):
        data = _args(
            body,
            {"meeting_id", "channel_id", "thread_id", "participants", "source"},
            {"meeting_id", "channel_id", "participants"},
        )
        meetings = service(request, "meetings")
        registered = (
            request.app.state.organization.get("meetings", {})
            .get("origins", {})
            .get(data["meeting_id"])
        )
        if not isinstance(registered, dict) or not registered.get("owner_id"):
            raise HTTPException(403, "Operator-verified meeting origin required")
        if actor != registered["owner_id"] and actor not in settings.approval_credentials:
            raise HTTPException(403, "Meeting organizer permission required")
        origin = {
            key: registered[key]
            for key in ("room_id", "channel_id", "thread_id", "message_id", "guild_id")
            if key in registered
        }
        supplied = data.get("source") or {}
        if (
            not isinstance(supplied, dict)
            or not settings.switch_room_id
            or origin.get("room_id") != settings.switch_room_id
            or not origin.get("message_id")
            or not origin.get("thread_id")
            or data["channel_id"] != origin.get("channel_id")
            or data.get("thread_id") != origin.get("thread_id")
            or any(key in origin and value != origin[key] for key, value in supplied.items())
        ):
            raise HTTPException(403, "Meeting origin does not match verified destination")
        origin["user_id"] = actor
        origin["project_id"] = settings.memory_project
        origin["origin_verification"] = "operator_registered_reference"
        result = meetings.create_meeting(
            data["meeting_id"],
            data["channel_id"],
            data.get("thread_id"),
            actor,
            data["participants"],
            source=origin,
        )
        return sanitize(
            {key: value for key, value in result.items() if key not in {"transcript", "notes"}}
        )

    @app.post("/meetings/{meeting_id}/consent")
    def consent(meeting_id: str, body: dict, request: Request, actor: Annotated[str, human]):
        data = _args(
            body,
            {"evidence_id", "expires_at", "disclosure_acknowledged"},
            {"evidence_id", "expires_at", "disclosure_acknowledged"},
        )
        return sanitize(
            service(request, "meetings").grant_consent(meeting_id, actor, actor, **data)
        )

    @app.post("/meetings/{meeting_id}/revoke")
    def revoke(meeting_id: str, request: Request, actor: Annotated[str, human]):
        return sanitize(service(request, "meetings").revoke_consent(meeting_id, actor, actor))

    @app.post("/meetings/{meeting_id}/cancel")
    def cancel(meeting_id: str, request: Request, actor: Annotated[str, human]):
        return sanitize(service(request, "meetings").cancel_meeting(meeting_id, actor))

    @app.post("/meetings/{meeting_id}/recording")
    async def recording(
        meeting_id: str, body: dict, request: Request, actor: Annotated[str, human]
    ):
        data = _args(body, {"filename"}, {"filename"})
        voice_id = request.app.state.organization.get("meetings", {}).get(
            "voice_agent_id"
        ) or settings.specialist_targets.get("voice", {}).get("agent_id")
        if not voice_id:
            raise HTTPException(409, "Independent Voice identity is not configured")
        return sanitize(
            service(request, "meetings").request_transcription(
                meeting_id, data["filename"], actor, voice_agent_id=voice_id
            )
        )

    @app.post("/meetings/{meeting_id}/notes")
    def request_notes(meeting_id: str, request: Request, actor: Annotated[str, human]):
        return sanitize(service(request, "meetings").request_notes(meeting_id, actor))

    @app.get("/meetings/{meeting_id}/notes")
    def read_notes(meeting_id: str, request: Request, actor: Annotated[str, human]):
        meetings = service(request, "meetings")
        meeting = meetings._meeting(meeting_id)
        meetings._actor(meeting, actor)
        meetings._consents(meeting_id)
        if not meeting.get("notes"):
            raise HTTPException(409, "Notes are not ready")
        import hashlib

        from .store import encode

        handoff = meetings.store.get_handoff(meeting.get("notes_handoff_id"))
        public = {key: value for key, value in meeting["notes"].items() if key != "segments"}
        if (
            not handoff
            or handoff["status"] != "completed"
            or not handoff["result"]
            or (
                handoff["result"].get("meeting_id") != meeting_id
                or handoff["result"].get("notes_sha256")
                != hashlib.sha256(encode(public).encode()).hexdigest()
            )
        ):
            raise HTTPException(409, "Notes Switch handoff is not complete")
        return sanitize(
            {key: value for key, value in meeting["notes"].items() if key != "segments"}
        )

    @app.post("/meetings/{meeting_id}/confirm")
    def confirm(meeting_id: str, body: dict, request: Request, actor: Annotated[str, human]):
        data = _args(body, {"notes_handoff_id", "actions"}, {"notes_handoff_id", "actions"})
        return sanitize(
            service(request, "meetings").confirm_actions(
                meeting_id, data["notes_handoff_id"], data["actions"], actor
            )
        )

    @app.post("/knowledge/import")
    def ingest(body: dict, request: Request, actor: Annotated[str, human]):
        data = _args(
            body,
            {
                "source",
                "project_id",
                "scope",
                "scope_id",
                "source_owner",
                "source_timestamp",
                "related_task_id",
            },
            {"source", "project_id", "source_owner"},
        )
        return sanitize(service(request, "knowledge").ingest_file(actor_id=actor, **data))

    @app.post("/knowledge/{document_id}/approve")
    def approve_document(document_id: str, request: Request, actor: Annotated[str, human]):
        return sanitize(service(request, "knowledge").approve_document(document_id, actor_id=actor))

    @app.post("/knowledge/{document_id}/reject")
    def reject_document(document_id: str, request: Request, actor: Annotated[str, human]):
        return sanitize(service(request, "knowledge").reject_document(document_id, actor_id=actor))

    @app.post("/knowledge/search")
    def knowledge_search(body: dict, request: Request, actor: Annotated[str, human]):
        data = _args(
            body, {"query", "project_id", "related_task_id", "limit"}, {"query", "project_id"}
        )
        return sanitize(service(request, "knowledge").retrieve(actor_id=actor, **data))

    @app.post("/lessons/propose")
    def propose_lesson(body: dict, request: Request, actor: Annotated[str, human]):
        data = _args(
            body,
            {
                "task_id",
                "project_id",
                "scope",
                "summary",
                "failure_signature",
                "root_cause",
                "root_cause_status",
                "correction",
                "regression_command",
                "applicability",
                "scope_id",
                "supersedes",
                "contradicts",
            },
            {
                "task_id",
                "project_id",
                "scope",
                "summary",
                "failure_signature",
                "root_cause",
                "root_cause_status",
                "correction",
                "regression_command",
                "applicability",
            },
        )
        return sanitize(service(request, "lessons").propose_from_task(actor_id=actor, **data))

    @app.post("/lessons/{lesson_id}/approve")
    async def approve_lesson(lesson_id: str, request: Request, actor: Annotated[str, human]):
        return sanitize(await service(request, "lessons").approve_lesson(lesson_id, actor_id=actor))

    @app.post("/lessons/search")
    async def lesson_search(body: dict, request: Request, actor: Annotated[str, human]):
        data = _args(body, {"query", "project_id", "task_id", "limit"}, {"query", "project_id"})
        return sanitize(await service(request, "lessons").retrieve_lessons(actor_id=actor, **data))

    @app.get("/tasks/{task_id}/change-report")
    def change_report(task_id: str, request: Request, actor: Annotated[str, human]):
        store = request.app.state.store
        task = store.get_task(task_id)
        if not task:
            raise HTTPException(404, "Task not found")
        if actor != task["source"].get("user_id") and actor not in settings.approval_credentials:
            raise HTTPException(403, "Task evidence reader permission required")
        rows = store.connection.execute(
            "SELECT h.result FROM handoffs h JOIN tasks t ON t.id=h.task_id "
            "WHERE json_extract(t.source,'$.evidence_task_id')=? "
            "AND h.status='completed' ORDER BY h.rowid DESC",
            (task_id,),
        ).fetchall()
        for row in rows:
            result = json.loads(row[0])
            if result.get("status") == "completed" and result.get("change_report"):
                return sanitize(result["change_report"])
        raise HTTPException(409, "No completed revision-pinned documentation report")

    @app.post("/tasks/{task_id}/employee")
    def assign_employee(task_id: str, body: dict, request: Request, actor: Annotated[str, human]):
        data = _args(body, {"employee_id", "expected_version"}, {"employee_id", "expected_version"})
        directory = service(request, "employees")
        store = request.app.state.store
        with store._lock, store.transaction():
            task = store._check(task_id, data["expected_version"])
            workflow = request.app.state.approvals.get(task_id)
            if task["status"] not in {"DRAFT", "AWAITING_APPROVAL"} or (
                workflow and workflow.get("phase") != "awaiting_engineering_approval"
            ):
                raise HTTPException(409, "Employee assignment must precede Engineering approval")
            entry = directory._entry(data["employee_id"])
            target = entry["switch_agent_id"]
            if workflow and target in {
                workflow["spec"][key] for key in ("pm", "qa", "reviewer", "release")
            }:
                raise HTTPException(409, "Employee Engineering identity must remain independent")
            result = directory.assign_task(
                task_id,
                data["employee_id"],
                assigned_by=actor,
                expected_version=data["expected_version"],
            )
            if workflow:
                workflow["spec"]["engineering"] = target
                request.app.state.approvals._save(task_id, workflow)
            store.connection.execute("UPDATE tasks SET version=version+1 WHERE id=?", (task_id,))
            return sanitize(
                {
                    "task_id": task_id,
                    "employee_id": result["employee_id"],
                    "status": result["status"],
                    "version": task["version"] + 1,
                }
            )

    @app.get("/watchdog")
    def watchdog_report(request: Request, actor: Annotated[str, human]):
        config = request.app.state.organization.get("watchdog")
        if not config or actor not in config.get("readers", []):
            raise HTTPException(403, "Watchdog reader permission required")
        from .watchdog import Watchdog

        return sanitize(Watchdog(request.app.state.store, config=config).poll_once())

    @app.post("/integrations/github/issues", status_code=202)
    async def github_issues(request: Request):
        maintainer = service(request, "oss")
        config = request.app.state.organization["oss"]
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 1024 * 1024:
                raise HTTPException(413, "Webhook exceeds configured limit")
        secret = os.environ.get(config.get("secret_env", "SWITCHCREW_GITHUB_WEBHOOK_SECRET"), "")
        return sanitize(
            await maintainer.accept_event(dict(request.headers), bytes(body), secret=secret)
        )

    @app.post("/integrations/github/webhook", status_code=202)
    async def github_webhook(request: Request):
        from .triggers import accept_github_webhook

        config = request.app.state.organization.get("github_triggers")
        if not config:
            raise HTTPException(409, "GitHub triggers are not configured")
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 1024 * 1024:
                raise HTTPException(413, "Webhook exceeds configured limit")
        secret = os.environ.get(config.get("secret_env", "SWITCHCREW_GITHUB_WEBHOOK_SECRET"), "")
        return sanitize(
            accept_github_webhook(
                request.app.state.store,
                dict(request.headers),
                bytes(body),
                secret=secret,
                repository=config["repository"],
                qa_recipient=config["qa_recipient"],
                security_recipient=config["security_recipient"],
                source_context=config.get("source", {}),
                sender=settings.pm_agent_id,
                project_id=settings.memory_project,
            )
        )
