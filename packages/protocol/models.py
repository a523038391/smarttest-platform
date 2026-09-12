from collections.abc import Iterator, Mapping
from datetime import datetime, timezone
from enum import Enum
import re
from types import MappingProxyType
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    PlainSerializer,
    model_validator,
)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must include a timezone")
    return value.astimezone(timezone.utc)


UtcDateTime = Annotated[datetime, AfterValidator(_as_utc)]
ProtocolVersion = Literal["1.0"]
ENVIRONMENT_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")


class FrozenMapping(Mapping[str, Any]):
    __slots__ = ("_data",)

    def __init__(self, values: Mapping[str, Any] | None = None) -> None:
        frozen = {key: _deep_freeze(value) for key, value in (values or {}).items()}
        object.__setattr__(self, "_data", MappingProxyType(frozen))

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def __setattr__(self, name: str, value: Any) -> None:
        raise TypeError("FrozenMapping is immutable")

    def __delattr__(self, name: str) -> None:
        raise TypeError("FrozenMapping is immutable")


def _deep_freeze(value: Any) -> Any:
    if isinstance(value, FrozenMapping):
        return value
    if isinstance(value, Mapping):
        return FrozenMapping(value)
    if isinstance(value, (list, tuple)):
        return tuple(_deep_freeze(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(_deep_freeze(item) for item in value)
    if isinstance(value, bytearray):
        return bytes(value)
    return value


def _deep_thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _deep_thaw(item) for key, item in value.items()}
    if isinstance(value, (tuple, frozenset)):
        return [_deep_thaw(item) for item in value]
    return value


def _freeze_mapping(value: Mapping[str, Any]) -> FrozenMapping:
    return FrozenMapping(value)


def _strip_idempotency_key(value: Any) -> Any:
    if isinstance(value, str):
        value = value.strip()
        if not value:
            raise ValueError("idempotency_key must not be blank")
    return value


ImmutableMapping = Annotated[
    Mapping[str, Any],
    AfterValidator(_freeze_mapping),
    PlainSerializer(_deep_thaw, return_type=dict[str, Any], when_used="json"),
]
ImmutableValue = Annotated[
    Any,
    AfterValidator(_deep_freeze),
    PlainSerializer(_deep_thaw, when_used="json"),
]
IdempotencyKey = Annotated[str, BeforeValidator(_strip_idempotency_key)]


class Engine(str, Enum):
    HTTP = "http"
    PYTEST = "pytest"
    PLAYWRIGHT = "playwright"


class EventType(str, Enum):
    STARTED = "started"
    PROGRESS = "progress"
    LOG = "log"
    ASSERTION = "assertion"
    EXTRACTION = "extraction"
    SCREENSHOT = "screenshot"
    ERROR = "error"
    HEARTBEAT = "heartbeat"
    ARTIFACT = "artifact"
    FINISHED = "finished"


class ResultOutcome(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    INFRA_ERROR = "infra_error"


class SecretReference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    category: Literal["environment_variable", "common_parameter"]
    name: str = Field(min_length=1, max_length=128)
    secret_ref: UUID


class AssertionRule(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source: Literal["status_code", "header", "json_path", "body", "response_time_ms"]
    expression: str | None = Field(default=None, max_length=1024)
    operator: Literal["eq", "ne", "gt", "gte", "lt", "lte", "contains", "matches"] = "eq"
    expected: ImmutableValue

    @model_validator(mode="after")
    def validate_expression(self) -> "AssertionRule":
        if self.source in {"header", "json_path"} and not self.expression:
            raise ValueError(f"{self.source} assertions require an expression")
        if self.source not in {"header", "json_path"} and self.expression is not None:
            raise ValueError(f"{self.source} assertions do not accept an expression")
        return self


class ExtractionRule(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(pattern=ENVIRONMENT_NAME_PATTERN.pattern)
    source: Literal["header", "json_path", "regex", "cookie"]
    expression: str = Field(min_length=1, max_length=1024)
    required: bool = True


class HttpStep(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1, max_length=128)
    request: ImmutableMapping
    assertions: tuple[AssertionRule, ...] = Field(default=(), max_length=50)
    extractions: tuple[ExtractionRule, ...] = Field(default=(), max_length=50)
    required: bool = True


class RetryPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    max_attempts: int = Field(default=1, ge=1, le=5)
    retry_on: tuple[ResultOutcome, ...] = (ResultOutcome.INFRA_ERROR, ResultOutcome.TIMED_OUT)
    backoff_seconds: int = Field(default=1, ge=0, le=300)
    max_backoff_seconds: int = Field(default=60, ge=0, le=600)
    allow_non_idempotent_http: bool = False

    @model_validator(mode="after")
    def validate_retry_outcomes(self) -> "RetryPolicy":
        if len(self.retry_on) != len(set(self.retry_on)):
            raise ValueError("retry outcomes must be unique")
        if any(item in {ResultOutcome.SUCCEEDED, ResultOutcome.CANCELLED} for item in self.retry_on):
            raise ValueError("successful or cancelled results cannot be retried")
        if self.max_backoff_seconds < self.backoff_seconds:
            raise ValueError("max_backoff_seconds cannot be less than backoff_seconds")
        return self


class ExecutionPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    before_steps: tuple[HttpStep, ...] = Field(default=(), max_length=20)
    assertions: tuple[AssertionRule, ...] = Field(default=(), max_length=50)
    extractions: tuple[ExtractionRule, ...] = Field(default=(), max_length=50)
    after_steps: tuple[HttpStep, ...] = Field(default=(), max_length=20)
    retry: RetryPolicy = Field(default_factory=RetryPolicy)

    @model_validator(mode="after")
    def validate_extraction_names(self) -> "ExecutionPolicy":
        rules = list(self.extractions)
        for step in (*self.before_steps, *self.after_steps):
            rules.extend(step.extractions)
        names = [item.name for item in rules]
        if len(names) != len(set(names)):
            raise ValueError("extraction names must be unique across an execution policy")
        return self


class Envelope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    protocol_version: ProtocolVersion = "1.0"
    run_id: UUID
    attempt_id: UUID
    engine: Engine


class TaskEnvelope(Envelope):
    task_id: UUID
    tenant_id: UUID
    project_id: UUID
    idempotency_key: IdempotencyKey = Field(min_length=1, max_length=255)
    created_at: UtcDateTime = Field(default_factory=lambda: datetime.now(timezone.utc))
    deadline: UtcDateTime
    entrypoint: str = Field(min_length=1, max_length=512)
    parameters: ImmutableMapping = Field(default_factory=FrozenMapping)
    run_spec_id: UUID | None = None
    source_ref: str | None = Field(default=None, min_length=1, max_length=512)
    content_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    environment_variables: ImmutableMapping = Field(default_factory=FrozenMapping)
    environment_id: UUID | None = None
    environment_revision: int | None = Field(default=None, ge=1)
    secret_references: tuple[SecretReference, ...] = ()
    execution_policy: ExecutionPolicy = Field(default_factory=ExecutionPolicy)

    @model_validator(mode="after")
    def deadline_follows_creation(self) -> "TaskEnvelope":
        if self.deadline <= self.created_at:
            raise ValueError("deadline must be after created_at")
        if (self.environment_id is None) != (self.environment_revision is None):
            raise ValueError("environment_id and environment_revision must be provided together")
        if self.secret_references and self.environment_id is None:
            raise ValueError("secret references require an environment")
        if any(
            not isinstance(name, str) or not ENVIRONMENT_NAME_PATTERN.fullmatch(name)
            or not isinstance(value, str)
            for name, value in self.environment_variables.items()
        ):
            raise ValueError("environment variables must have valid names and string values")
        identities = [(item.category, item.name) for item in self.secret_references]
        if len(identities) != len(set(identities)):
            raise ValueError("secret references must be unique")
        reference_ids = [item.secret_ref for item in self.secret_references]
        if len(reference_ids) != len(set(reference_ids)):
            raise ValueError("secret reference IDs must be unique")
        if self.engine is not Engine.HTTP and (
            self.execution_policy.before_steps
            or self.execution_policy.after_steps
            or self.execution_policy.assertions
            or self.execution_policy.extractions
        ):
            raise ValueError("HTTP steps, assertions, and extractions require the HTTP engine")
        return self


class EventEnvelope(Envelope):
    event_id: UUID
    seq: int = Field(ge=0)
    occurred_at: UtcDateTime = Field(default_factory=lambda: datetime.now(timezone.utc))
    type: EventType
    payload: ImmutableMapping = Field(default_factory=FrozenMapping)


class ResultEnvelope(Envelope):
    outcome: ResultOutcome
    completed_at: UtcDateTime = Field(default_factory=lambda: datetime.now(timezone.utc))
    duration_ms: int = Field(ge=0)
    summary: ImmutableMapping = Field(default_factory=FrozenMapping)