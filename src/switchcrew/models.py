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
