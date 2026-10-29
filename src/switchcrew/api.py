"""Authenticated, versioned task APIs with sanitized errors and structured logs."""

import logging
import secrets
from contextlib import asynccontextmanager
from enum import StrEnum
from typing import Annotated, Any
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from switchcrew.logging import configure_logging
from switchcrew.models import TaskStatus
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
        for field in (settings.api_token, settings.pm_api_key, settings.research_api_key)
        if field is not None
    ]

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

    @app.exception_handler(KeyError)
    async def missing_record(request: Request, exc: Exception):
        return JSONResponse(status_code=404, content={"detail": "Task not found"})

    @app.get("/health", response_model=HealthResponse)
    def health(request: Request):
        # Exercise the database as well as the HTTP process.
        request.app.state.store.healthcheck()
        return {"status": "ok"}

    @app.get("/openapi.json", dependencies=authenticated, include_in_schema=False)
    def schema():
        return app.openapi()

    @app.post("/tasks", dependencies=authenticated, status_code=201, response_model=TaskResponse)
    def create_task(body: TaskCreate, request: Request):
        task = request.app.state.store.create_task(
            body.objective,
            body.source.model_dump(exclude_none=True),
            body.acceptance_criteria,
        )
        return sanitize(task)

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
        return sanitize(
            request.app.state.store.transition_task(
                task_id,
                body.status,
                body.expected_version,
            )
        )

    return app
