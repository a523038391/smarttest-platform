from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from .data_factory_schemas import VARIABLE_PATTERN, _bounded_json
from .load_test_domain import (
    LoadTestMode, LoadTestRecord, LoadTestRunRecord, LoadTestTrafficMode,
)
from .schemas import ApiModel


HTTP_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"})


class LoadTestTarget(ApiModel):
    name: str = Field(min_length=1, max_length=255)
    method: str = "GET"
    url: str = Field(min_length=1, max_length=8_192)
    headers: dict[str, Any] = Field(default_factory=dict, max_length=100)
    query: dict[str, Any] = Field(default_factory=dict, max_length=100)
    body: Any = None
    expected_statuses: list[int] = Field(
        default_factory=lambda: list(range(200, 300)), min_length=1, max_length=500,
    )
    extractors: dict[str, str] = Field(default_factory=dict, max_length=100)
    think_time_ms: int = Field(default=0, ge=0, le=60_000)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("target name must not be blank")
        return value

    @field_validator("method")
    @classmethod
    def validate_method(cls, value: str) -> str:
        value = value.upper()
        if value not in HTTP_METHODS:
            raise ValueError("unsupported HTTP method")
        return value

    @field_validator("headers", "query", "body")
    @classmethod
    def validate_json(cls, value: Any) -> Any:
        return _bounded_json(value)

    @field_validator("expected_statuses")
    @classmethod
    def validate_statuses(cls, value: list[int]) -> list[int]:
        if any(code < 100 or code > 599 for code in value) or len(set(value)) != len(value):
            raise ValueError("expected_statuses must contain unique HTTP status codes")
        return value

    @field_validator("extractors")
    @classmethod
    def validate_extractors(cls, value: dict[str, str]) -> dict[str, str]:
        for name, path in value.items():
            if not VARIABLE_PATTERN.fullmatch(name):
                raise ValueError("extractor variable name is invalid")
            if len(path) > 1_024 or not path or any(not part for part in path.split(".")):
                raise ValueError("extractor response path is invalid")
        return value


class _LoadTestBody(ApiModel):
    name: str = Field(min_length=1, max_length=255)
    description: str = Field(default="", max_length=100_000)
    targets: list[LoadTestTarget] = Field(min_length=1, max_length=20)
    mode: LoadTestMode
    request_count: int = Field(default=1, ge=1, le=10_000)
    duration_seconds: int = Field(default=1, ge=1, le=1_800)
    concurrency: int = Field(default=1, ge=1, le=100)
    interval_ms: int = Field(default=0, ge=0, le=60_000)
    timeout_seconds: float = Field(default=30, gt=0, le=120)
    environment_id: UUID | None = None
    traffic_mode: LoadTestTrafficMode = LoadTestTrafficMode.REQUESTS
    initial_variables: dict[str, Any] = Field(default_factory=dict, max_length=1_000)
    stop_on_failure: bool = True

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("load test name must not be blank")
        return value

    @field_validator("initial_variables")
    @classmethod
    def validate_initial_variables(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _bounded_json(value)

    @model_validator(mode="after")
    def validate_targets(self) -> "_LoadTestBody":
        names = [target.name for target in self.targets]
        if len(names) != len(set(names)):
            raise ValueError("target names must be unique")
        if (self.traffic_mode is not LoadTestTrafficMode.SCENARIO
                and any(target.extractors for target in self.targets)):
            raise ValueError("extractors are only supported in SCENARIO traffic mode")
        _bounded_json([target.model_dump(mode="json") for target in self.targets])
        return self


class LoadTestCreate(_LoadTestBody):
    project_id: UUID


class LoadTestUpdate(_LoadTestBody):
    state_version: int = Field(ge=0)


class LoadTestResponse(_LoadTestBody):
    id: UUID
    project_id: UUID
    state_version: int
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_record(cls, record: LoadTestRecord) -> "LoadTestResponse":
        return cls.model_validate(record, from_attributes=True)


class LoadTestListResponse(ApiModel):
    items: list[LoadTestResponse]
    total: int


class LoadTestRunResponse(ApiModel):
    id: UUID
    load_test_id: UUID
    status: Literal["RUNNING", "SUCCEEDED", "FAILED"]
    mode: LoadTestMode
    started_at: datetime
    finished_at: datetime | None
    metrics: dict[str, Any]
    error_message: str | None

    @classmethod
    def from_record(cls, record: LoadTestRunRecord) -> "LoadTestRunResponse":
        return cls.model_validate(record, from_attributes=True)


class LoadTestRunListResponse(ApiModel):
    items: list[LoadTestRunResponse]
    total: int