from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from packages.protocol import Engine, EventType, ResultOutcome

from .domain import (
    AttemptRecord,
    AttemptState,
    ArtifactRecord,
    EventRecord,
    RunRecord,
    RunState,
)


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HealthResponse(ApiModel):
    status: str


class RunCreate(ApiModel):
    engine: Engine
    parameters: dict[str, Any] = Field(default_factory=dict)


class RunResponse(ApiModel):
    id: UUID
    engine: Engine
    parameters: dict[str, Any]
    state: RunState
    created_at: datetime
    updated_at: datetime
    state_version: int

    @classmethod
    def from_record(cls, run: RunRecord) -> "RunResponse":
        return cls.model_validate(run, from_attributes=True)


class RunListResponse(ApiModel):
    items: list[RunResponse]
    total: int


class AttemptResponse(ApiModel):
    id: UUID
    run_id: UUID
    state: AttemptState
    created_at: datetime
    updated_at: datetime
    state_version: int
    outcome: ResultOutcome | None
    duration_ms: int | None
    summary: dict[str, Any] | None
    completed_at: datetime | None
    runner_id: str | None
    heartbeat_at: datetime | None
    lease_expires_at: datetime | None

    @classmethod
    def from_record(cls, attempt: AttemptRecord) -> "AttemptResponse":
        return cls.model_validate(attempt, from_attributes=True)


class AttemptListResponse(ApiModel):
    items: list[AttemptResponse]
    total: int


class EventResponse(ApiModel):
    cursor: int
    event_id: UUID
    run_id: UUID
    attempt_id: UUID
    engine: Engine
    seq: int
    occurred_at: datetime
    type: EventType
    payload: dict[str, Any]

    @classmethod
    def from_record(cls, event: EventRecord) -> "EventResponse":
        return cls.model_validate(event, from_attributes=True)


class EventListResponse(ApiModel):
    items: list[EventResponse]
    total: int
    next_cursor: int


class ArtifactResponse(ApiModel):
    id: UUID
    run_id: UUID
    attempt_id: UUID
    kind: str
    name: str
    content_type: str
    size_bytes: int
    created_at: datetime
    download_url: str

    @classmethod
    def from_record(cls, artifact: ArtifactRecord) -> "ArtifactResponse":
        return cls(
            id=artifact.id,
            run_id=artifact.run_id,
            attempt_id=artifact.attempt_id,
            kind=artifact.kind,
            name=artifact.name,
            content_type=artifact.content_type,
            size_bytes=artifact.size_bytes,
            created_at=artifact.created_at,
            download_url=(
                f"/api/v1/runs/{artifact.run_id}/artifacts/{artifact.id}/content"
            ),
        )


class ArtifactListResponse(ApiModel):
    items: list[ArtifactResponse]
    total: int


class ValidationErrorDetail(ApiModel):
    loc: list[str | int]
    message: str
    type: str


class Problem(ApiModel):
    type: str = "about:blank"
    title: str
    status: int
    detail: str
    instance: str
    code: str
    errors: list[ValidationErrorDetail] | None = None