"""Pipeline execution result models — Pydantic V1 compatible."""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field

from config.settings import utcnow


class PipelineStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    PARTIAL = "partial"


class ExecutionStep(BaseModel):
    step_name: str
    status: PipelineStatus
    started_at: datetime = Field(default_factory=utcnow)
    ended_at: datetime | None = None
    records_processed: int = 0
    errors: list[str] = Field(default_factory=list)
    details: str | None = None


class PipelineResult(BaseModel):
    pipeline_id: str = Field(default_factory=lambda: f"PL-{utcnow().strftime('%Y%m%d-%H%M%S')}")
    status: PipelineStatus = PipelineStatus.PENDING
    steps: list[ExecutionStep] = Field(default_factory=list)
    total_records: int = 0
    succeeded: int = 0
    failed: int = 0
    unresolved_contacts: int = 0
    started_at: datetime = Field(default_factory=utcnow)
    completed_at: datetime | None = None
    error: str | None = None
