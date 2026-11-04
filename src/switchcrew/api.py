"""Authenticated, versioned task APIs with sanitized errors and structured logs."""

import logging
import secrets
from contextlib import asynccontextmanager
from enum import StrEnum
from typing import Annotated, Any, Literal
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from switchcrew.approval import ApprovalWorkflow
from switchcrew.logging import configure_logging
from switchcrew.models import TaskStatus
from switchcrew.orchestration import Orchestrator
from switchcrew.settings import Settings
from switchcrew.store import ConflictError, InvalidTransitionError, Store

logger = logging.getLogger("switchcrew.api")


class RequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class SourceContext(RequestModel):
    channel_id: str | None = Field(default=None, max_length=200)
    thread_id: str | None = Field(default=None, max_length=200)
    message_id: str | None = Field(default=None, max_length=200)
    room_id: str | None = Field(default=None, max_length=200)


class TaskCreate(RequestModel):
    kind: Literal["research", "engineering", "orchestrate"] = "research"
    job_key: str | None = Field(default=None, min_length=1, max_length=200)
    objective: str = Field(min_length=1, max_length=10000)
    acceptance_criteria: list[Annotated[str, Field(min_length=1, max_length=2000)]] = Field(
        min_length=1, max_length=30
    )
    source: SourceContext = Field(default_factory=SourceContext)

    @field_validator("acceptance_criteria")
    @classmethod
    def nonblank_criteria(cls, values: list[str]) -> list[str]:
        if any(not value.strip() for value in values):
            raise ValueError("Acceptance criteria cannot be blank")
        return [value.strip() for value in values]


class ApprovalDecision(RequestModel):
    stage: Literal["engineering", "release"]
    approved: bool = Field(strict=True)
    expected_version: int = Field(ge=0)


class DayOneStatus(StrEnum):
    NEEDS_CLARIFICATION = "NEEDS_CLARIFICATION"
    PLANNED = "PLANNED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    BLOCKED = "BLOCKED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"


class TaskTransition(RequestModel):
    status: DayOneStatus
    expected_version: int = Field(ge=0)


class TaskResponse(BaseModel):
    id: str
    objective: str
    source: dict
    acceptance_criteria: list[str]
    status: TaskStatus
    version: int = Field(ge=0)
    created_at: str


class TaskEventResponse(BaseModel):
    id: int
    task_id: str
    type: str
    details: dict
    version: int = Field(ge=0)
    created_at: str


class HealthResponse(BaseModel):
    status: str


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    if settings.api_token is None:
        raise RuntimeError("Configure SWITCHCREW_API_TOKEN before starting the API")
    token = settings.api_token.get_secret_value()
    credentials = [
        field.get_secret_value()
        for field in (
            settings.api_token,
            settings.pm_api_key,
            settings.research_api_key,
            settings.waggle_token,
            settings.company_brain_token,
        )
        if field is not None
    ]

    human_tokens = {
        actor: credential.get_secret_value()
        for actor, credential in settings.approval_credentials.items()
    }
    actor_tokens = {
        actor: credential.get_secret_value()
        for actor, credential in settings.human_credentials.items()
    }
    if set(actor_tokens) & set(human_tokens):
        raise RuntimeError("Configure each human identity in only one credential map")
    if set(actor_tokens.values()) & (set(human_tokens.values()) | set(credentials)):
        raise RuntimeError("Human identity credentials must be independent")
    actor_tokens.update(human_tokens)
    worker_tokens = set(credentials)
    if any(value in worker_tokens for value in human_tokens.values()):
        raise RuntimeError("Human approval credentials must be distinct from service credentials")
    credentials.extend(human_tokens.values())
    credentials.extend(actor_tokens.values())

    def sanitize(value: Any) -> Any:
        if isinstance(value, str):
            for credential in credentials:
                if credential:
                    value = value.replace(credential, "[REDACTED]")
            return value
        if isinstance(value, (list, tuple)):
            return [sanitize(item) for item in value]
        if isinstance(value, dict):
            return {key: sanitize(item) for key, item in value.items()}
        return value

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        configure_logging()
        settings.database_path.parent.mkdir(parents=True, exist_ok=True)
        app.state.store = Store(settings.database_path)
        app.state.approvals = ApprovalWorkflow(app.state.store, human_tokens)
        app.state.company_brain = None
        from switchcrew.harnesses import HarnessPlanner, load_harness_config

        harness_roles = load_harness_config(settings.harness_config)
        harness_planner = (
            HarnessPlanner(
                harness_roles["pm"],
                available_roles=set(harness_roles),
                max_calls=settings.harness_planner_max_calls,
                max_steps=settings.harness_planner_max_steps,
                max_prompt_chars=settings.harness_planner_max_prompt_chars,
                fallback_on_failure=settings.harness_planner_fallback,
            )
            if "pm" in harness_roles
            else None
        )
        if settings.company_brain_enabled:
            from switchcrew.company_brain import company_brain_from_settings

            app.state.company_brain = company_brain_from_settings(settings)
        targets = {
            "research": settings.research_agent_id,
            "engineering": settings.engineering_agent_id,
        }
        targets.update(
            {role: spec.get("agent_id", "") for role, spec in settings.specialist_targets.items()}
        )
        app.state.orchestrator = Orchestrator(
            app.state.store,
            settings.pm_agent_id,
            targets,
            engineering_route={
                "qa": settings.qa_agent_id,
                "reviewer": settings.reviewer_agent_id,
                "max_corrections": settings.max_corrections,
                "release": settings.release_agent_id,
                "job_key": settings.orchestration_job_key,
                "repository": settings.release_repository,
                "base": settings.release_base,
            },
            company_brain=app.state.company_brain,
            planner=harness_planner,
        )
        from .organization import knowledge_services, load_organization_config, meeting_service

        app.state.organization = load_organization_config(settings.organization_config)
        from .organization import configure_security

        configure_security(app.state.store, settings, app.state.organization)

        app.state.meetings = meeting_service(
            app.state.store, settings, app.state.organization, enable_voice_provider=False
        )
        app.state.knowledge, app.state.lessons = knowledge_services(
            app.state.store, settings, app.state.organization
        )
        from .organization import employee_directory, oss_service

        app.state.employees = employee_directory(app.state.store, app.state.organization)
        app.state.oss = oss_service(
            app.state.store, settings, app.state.organization, app.state.company_brain
        )

        yield
        close = getattr(app.state.store, "close", None)
        if close:
            close()

    app = FastAPI(
        title="SwitchCrew",
        version="0.1.0",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    def authenticate(authorization: Annotated[str | None, Header()] = None) -> None:
        supplied = authorization.removeprefix("Bearer ") if authorization else ""
        if (
            not authorization
            or not authorization.startswith("Bearer ")
            or not secrets.compare_digest(supplied.encode(), token.encode())
        ):
            raise HTTPException(401, "Unauthorized", headers={"WWW-Authenticate": "Bearer"})

    def authenticate_human(authorization: Annotated[str | None, Header()] = None) -> str:
        supplied = authorization.removeprefix("Bearer ") if authorization else ""
        if authorization and authorization.startswith("Bearer "):
            for actor, credential in human_tokens.items():
                if secrets.compare_digest(supplied.encode(), credential.encode()):
                    return actor
        raise HTTPException(403, "Authorized human approval credential required")

    def authenticate_actor(authorization: Annotated[str | None, Header()] = None) -> str:
        supplied = authorization.removeprefix("Bearer ") if authorization else ""
        if authorization and authorization.startswith("Bearer "):
            for actor, credential in actor_tokens.items():
                if secrets.compare_digest(supplied.encode(), credential.encode()):
                    return actor
        raise HTTPException(403, "Authenticated human identity required")

    authenticated = [Depends(authenticate)]

    @app.middleware("http")
    async def request_logging(request: Request, call_next):
        request_id = uuid4().hex
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        logger.info(
            "request",
            extra={
                "request_id": request_id,
                "method": request.method,
                "status_code": response.status_code,
            },
        )
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        # Pydantic's default input/ctx fields can include passwords and tokens.
        errors = [{"loc": item["loc"], "type": item["type"]} for item in exc.errors()]
        return JSONResponse(status_code=422, content=sanitize({"detail": errors}))

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception):
        error_name = type(exc).__name__
        logger.error(
            "request_failed",
            extra={
                "request_id": getattr(request.state, "request_id", ""),
                "error_type": error_name,
            },
        )
        return JSONResponse(status_code=500, content={"detail": "Internal server error"})

    @app.exception_handler(ConflictError)
    @app.exception_handler(InvalidTransitionError)
    async def state_conflict(request: Request, exc: Exception):
        return JSONResponse(status_code=409, content={"detail": "Task state conflict"})

    @app.exception_handler(PermissionError)
    async def forbidden(request: Request, exc: Exception):
        return JSONResponse(
            status_code=403, content={"detail": "Approval or QA policy denied action"}
        )

    @app.exception_handler(KeyError)
    async def missing_record(request: Request, exc: Exception):
        return JSONResponse(status_code=404, content={"detail": "Task not found"})

    @app.exception_handler(ValueError)
    async def invalid_workflow_input(request: Request, exc: Exception):
        return JSONResponse(status_code=422, content={"detail": "Invalid workflow input"})

    @app.get("/health", response_model=HealthResponse)
    def health(request: Request):
        # Exercise the database as well as the HTTP process.
        request.app.state.store.healthcheck()
        return {"status": "ok"}

    @app.get("/openapi.json", dependencies=authenticated, include_in_schema=False)
    def schema():
        return app.openapi()

    @app.get("/dashboard", include_in_schema=False)
    def operator_dashboard():
        from .dashboard import DASHBOARD_PAGE

        return HTMLResponse(
            DASHBOARD_PAGE,
            headers={
                "Cache-Control": "no-store",
                "Content-Security-Policy": (
                    "default-src 'none'; style-src 'unsafe-inline'; "
                    "script-src 'unsafe-inline'; connect-src 'self'; base-uri 'none'; "
                    "form-action 'none'; frame-ancestors 'none'"
                ),
                "X-Content-Type-Options": "nosniff",
            },
        )

    @app.get("/operator/snapshot", dependencies=authenticated)
    def operator_snapshot(request: Request, limit: int = 40):
        from .operator_dashboard import snapshot

        return sanitize(snapshot(request.app.state.store, settings, limit=limit))

    @app.post("/discord/interactions", include_in_schema=False)
    async def discord_interaction(request: Request):
        from .discord_interactions import respond_to_interaction

        if not settings.discord_application_public_key:
            raise HTTPException(503, "Discord interactions are not provisioned")
        content = bytearray()
        async for chunk in request.stream():
            content.extend(chunk)
            if len(content) > 32768:
                raise HTTPException(413, "Discord interaction too large")
        try:
            return respond_to_interaction(
                request.app.state.store,
                settings,
                bytes(content),
                {k.lower(): v for k, v in request.headers.items()},
            )
        except PermissionError:
            raise HTTPException(401, "Discord signature or scope denied") from None
        except (ValueError, TypeError, KeyError):
            raise HTTPException(400, "Invalid Discord interaction") from None

    @app.post("/tasks", dependencies=authenticated, status_code=201, response_model=TaskResponse)
    async def create_task(body: TaskCreate, request: Request):
        if body.kind == "orchestrate":
            if not settings.pm_agent_id:
                raise HTTPException(409, "PM orchestration is not configured")
            if len(body.acceptance_criteria) > 12:
                raise HTTPException(422, "Orchestration supports at most 12 criteria")
        if body.kind == "engineering" and not all(
            (
                body.job_key,
                settings.pm_agent_id,
                settings.engineering_agent_id,
                settings.qa_agent_id,
                settings.reviewer_agent_id,
                settings.release_agent_id,
                settings.release_repository,
                settings.release_base,
            )
        ):
            raise HTTPException(409, "Engineering route is not configured")
        task = request.app.state.store.create_task(
            body.objective,
            {**body.source.model_dump(exclude_none=True), "project_id": settings.memory_project},
            body.acceptance_criteria,
        )
        if body.kind == "orchestrate":
            from switchcrew.harnesses import HarnessError

            try:
                await request.app.state.orchestrator.start_with_planner(task["id"])
            except (HarnessError, ValueError):
                raise HTTPException(503, "Configured PM harness unavailable or invalid") from None
            await request.app.state.orchestrator.reconcile_with_context(task["id"])
            task = request.app.state.store.get_task(task["id"])
        if body.kind == "engineering":
            brain_metadata = None
            brain_client = request.app.state.company_brain
            if brain_client is not None:
                from switchcrew.company_brain import capture_engineering_context

                query = (
                    body.objective
                    + "\nAcceptance criteria:\n"
                    + "\n".join(body.acceptance_criteria)
                )
                brain_metadata = await capture_engineering_context(brain_client, query)
            latest = request.app.state.store.get_task(task["id"])
            if latest and latest["status"] == "DRAFT":
                try:
                    request.app.state.approvals.request(
                        task["id"],
                        settings.pm_agent_id,
                        settings.engineering_agent_id,
                        settings.qa_agent_id,
                        settings.release_agent_id,
                        body.job_key,
                        settings.release_repository,
                        settings.release_base,
                        reviewer=settings.reviewer_agent_id,
                        max_corrections=settings.max_corrections,
                        company_brain=brain_metadata,
                    )
                except ConflictError:
                    latest = request.app.state.store.get_task(task["id"])
                    if not latest or latest["status"] not in {
                        "CANCELLED",
                        "COMPLETED",
                        "FAILED",
                        "BLOCKED",
                    }:
                        raise
            task = request.app.state.store.get_task(task["id"])
        return sanitize(task)

    @app.get("/tasks", dependencies=authenticated)
    def list_tasks(
        request: Request,
        status: str | None = None,
        employee: str | None = None,
        channel_id: str | None = None,
        limit: int = 30,
    ):
        if not 1 <= limit <= 100:
            raise HTTPException(422, "Limit must be between 1 and 100")
        query, values = (
            "SELECT * FROM tasks WHERE json_extract(source,'$.command_receipt') IS NULL",
            [],
        )
        if status is not None:
            if status not in {item.value for item in TaskStatus}:
                raise HTTPException(422, "Unknown task status")
            query += " AND status=?"
            values.append(status)
        if employee is not None:
            has_directory = request.app.state.store.connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='employee_assignments'"
            ).fetchone()
            query += (
                " AND EXISTS (SELECT 1 FROM employee_assignments ea WHERE "
                "ea.task_id=tasks.id AND ea.employee_id=? AND ea.status='active')"
                if has_directory
                else " AND json_extract(source,'$.assigned_employee')=?"
            )
            values.append(employee)
        if channel_id is not None:
            query += " AND json_extract(source,'$.channel_id')=?"
            values.append(channel_id)
        query += " ORDER BY created_at DESC,rowid DESC LIMIT ?"
        values.append(limit)
        with request.app.state.store._lock:
            return sanitize(
                [
                    request.app.state.store._row(row)
                    for row in request.app.state.store.connection.execute(query, values)
                ]
            )

    @app.get("/tasks/{task_id}/trace", dependencies=authenticated)
    def get_trace(task_id: str, request: Request):
        from .trace import execution_trace

        try:
            return sanitize(execution_trace(request.app.state.store, task_id))
        except KeyError:
            raise HTTPException(404, "Task not found") from None

    @app.get("/tasks/{task_id}", dependencies=authenticated, response_model=TaskResponse)
    def get_task(task_id: str, request: Request):
        task = request.app.state.store.get_task(task_id)
        if task is None:
            raise HTTPException(404, "Task not found")
        return sanitize(task)

    @app.get(
        "/tasks/{task_id}/events",
        dependencies=authenticated,
        response_model=list[TaskEventResponse],
    )
    def get_events(task_id: str, request: Request):
        if request.app.state.store.get_task(task_id) is None:
            raise HTTPException(404, "Task not found")
        return sanitize(request.app.state.store.list_events(task_id))

    @app.post(
        "/tasks/{task_id}/transitions", dependencies=authenticated, response_model=TaskResponse
    )
    def transition_task(task_id: str, body: TaskTransition, request: Request):
        if (
            request.app.state.approvals.get(task_id) or request.app.state.orchestrator.get(task_id)
        ) and body.status != "CANCELLED":
            raise HTTPException(409, "Engineering lifecycle is controlled by approvals and QA")
        return sanitize(
            request.app.state.store.transition_task(
                task_id,
                body.status,
                body.expected_version,
            )
        )

    @app.get("/tasks/{task_id}/plan", dependencies=authenticated)
    async def get_plan(task_id: str, request: Request):
        data = await request.app.state.orchestrator.reconcile_with_context(task_id)
        if data is None:
            raise HTTPException(404, "Orchestration plan not found")
        return sanitize(data)

    @app.get("/tasks/{task_id}/workflow", dependencies=authenticated)
    def get_workflow(task_id: str, request: Request):
        data = request.app.state.approvals.reconcile(task_id)
        if data is None:
            raise HTTPException(404, "Engineering workflow not found")
        result = dict(data)
        if data.get("qa_handoff"):
            result["qa_result"] = request.app.state.store.get_handoff(data["qa_handoff"])["result"]
        if data.get("review_handoff"):
            result["review_result"] = request.app.state.store.get_handoff(data["review_handoff"])[
                "result"
            ]
        return sanitize(result)

    @app.post("/tasks/{task_id}/approvals")
    def decide(
        task_id: str,
        body: ApprovalDecision,
        request: Request,
        actor: Annotated[str, Depends(authenticate_human)],
    ):
        request.app.state.approvals.reconcile(task_id)
        result = request.app.state.approvals.decide(
            task_id,
            body.stage,
            body.approved,
            actor,
            body.expected_version,
        )
        return sanitize(result)

    from .organization_api import install_organization_routes

    install_organization_routes(app, settings, authenticate_actor, sanitize)
    return app
