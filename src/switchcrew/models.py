"""Validated persistence contracts."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class TaskStatus(StrEnum):
    DRAFT = "DRAFT"
    NEEDS_CLARIFICATION = "NEEDS_CLARIFICATION"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    PLANNED = "PLANNED"
    IN_PROGRESS = "IN_PROGRESS"
    QA_REVIEW = "QA_REVIEW"
    PASSED = "PASSED"
    HUMAN_REVIEW = "HUMAN_REVIEW"
    PUBLISH_APPROVED = "PUBLISH_APPROVED"
    PUBLISHING = "PUBLISHING"
    PR_OPEN = "PR_OPEN"
    COMPLETED = "COMPLETED"
    BLOCKED = "BLOCKED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"


class TaskCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    objective: str = Field(min_length=1, max_length=20000)
    source: dict = Field(default_factory=dict)
    acceptance_criteria: list[str] = Field(default_factory=list)


class ReviewResult(BaseModel):
    """Revision-bound independent reviewer receipt persisted on a handoff."""

    model_config = ConfigDict(extra="forbid", strict=True)
    status: str
    implementation_assignment: str = Field(min_length=1)
    reviewed_revision: str = Field(min_length=1)
    base_revision: str = Field(min_length=1)
    diff_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    findings: list[dict]
    commands: list[dict] = Field(min_length=1)
