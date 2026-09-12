from datetime import datetime
from typing import Any, Literal
from uuid import UUID, uuid4

from packages.protocol import Engine, ExecutionPolicy, SecretReference
from pydantic import Field, field_validator, model_validator

from .schemas import ApiModel
from .test_plan_domain import (
    ExecutionBatchRecord, RunSpecRecord, TestPlanDataRow, TestPlanItem,
    TestPlanRecord, TestPlanStatus,
)
from .test_plan_repository import normalize_plan_items


SINGLE_TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")


class TestPlanDataRowWrite(ApiModel):
    row_key: str = Field(min_length=1, max_length=255)
    values: dict[str, Any] = Field(default_factory=dict)

    def to_record(self) -> TestPlanDataRow:
        return TestPlanDataRow(self.row_key, self.values)


class TestPlanItemWrite(ApiModel):
    item_id: UUID = Field(default_factory=uuid4)
    script_id: UUID
    script_revision: int = Field(ge=1)
    environment_id: UUID | None = None
    environment_revision: int | None = Field(default=None, ge=1)
    default_parameters: dict[str, Any] = Field(default_factory=dict)
    data_rows: list[TestPlanDataRowWrite] | None = Field(default=None, max_length=1000)
    execution_policy: ExecutionPolicy = Field(default_factory=ExecutionPolicy)

    @model_validator(mode="after")
    def validate_environment_pin(self) -> "TestPlanItemWrite":
        if (self.environment_id is None) != (self.environment_revision is None):
            raise ValueError("environment_id and environment_revision must be provided together")
        return self

    def to_record(self) -> TestPlanItem:
        return TestPlanItem(
            self.item_id, self.script_id, self.script_revision,
            self.environment_id, self.environment_revision,
            self.default_parameters,
            tuple(row.to_record() for row in (self.data_rows or [])),
            self.execution_policy,
        )


class _TestPlanBody(ApiModel):
    name: str = Field(min_length=1, max_length=255)
    description: str = Field(default="", max_length=100_000)
    items: list[TestPlanItemWrite] = Field(min_length=1, max_length=100)
    max_parallel: int = Field(default=10, ge=1, le=100)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("test plan name must not be blank")
        return normalized

    @model_validator(mode="after")
    def validate_definition(self) -> "_TestPlanBody":
        normalize_plan_items([item.to_record() for item in self.items])
        return self

    def records(self) -> tuple[TestPlanItem, ...]:
        return tuple(item.to_record() for item in self.items)


class TestPlanCreate(_TestPlanBody):
    tenant_id: UUID = SINGLE_TENANT_ID
    project_id: UUID


class TestPlanUpdate(_TestPlanBody):
    status: TestPlanStatus
    state_version: int = Field(ge=0)


class TestPlanExecutionCreate(ApiModel):
    execution_target: Literal["docker", "host"] = "docker"
    project_directory: str | None = Field(default=None, min_length=1, max_length=2_048)
    python_executable: str | None = Field(default=None, min_length=1, max_length=2_048)

    @field_validator("project_directory", "python_executable")
    @classmethod
    def validate_host_path(cls, value: str | None) -> str | None:
        if value is not None and (
            value != value.strip() or any(ord(character) < 32 for character in value)
        ):
            raise ValueError("host paths must not contain padding or controls")
        return value

    @model_validator(mode="after")
    def validate_target_fields(self) -> "TestPlanExecutionCreate":
        paths = (self.project_directory, self.python_executable)
        if self.execution_target == "host" and any(value is None for value in paths):
            raise ValueError(
                "host execution requires project_directory and python_executable"
            )
        if self.execution_target == "docker" and any(value is not None for value in paths):
            raise ValueError(
                "docker execution does not accept project_directory or python_executable"
            )
        return self


class TestPlanDataRowResponse(ApiModel):
    row_key: str
    values: dict[str, Any]


class TestPlanItemResponse(ApiModel):
    item_id: UUID
    script_id: UUID
    script_revision: int
    environment_id: UUID | None
    environment_revision: int | None
    default_parameters: dict[str, Any]
    data_rows: list[TestPlanDataRowResponse]
    execution_policy: ExecutionPolicy


class TestPlanResponse(ApiModel):
    id: UUID
    tenant_id: UUID
    project_id: UUID
    name: str
    description: str
    items: list[TestPlanItemResponse]
    max_parallel: int
    status: TestPlanStatus
    revision: int
    state_version: int
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_record(cls, record: TestPlanRecord) -> "TestPlanResponse":
        return cls.model_validate(record, from_attributes=True)


class TestPlanListResponse(ApiModel):
    items: list[TestPlanResponse]
    total: int


class ExecutionBatchResponse(ApiModel):
    id: UUID
    tenant_id: UUID
    project_id: UUID
    plan_id: UUID
    plan_revision: int
    idempotency_key: str
    max_parallel: int
    run_ids: list[UUID]
    created_at: datetime

    @classmethod
    def from_record(cls, record: ExecutionBatchRecord) -> "ExecutionBatchResponse":
        return cls.model_validate(record, from_attributes=True)


class RunSpecResponse(ApiModel):
    id: UUID
    run_id: UUID
    batch_id: UUID
    tenant_id: UUID
    project_id: UUID
    plan_id: UUID
    plan_revision: int
    item_id: UUID
    row_key: str
    engine: Engine
    script_id: UUID
    script_revision: int
    source_ref: str
    content_digest: str
    entrypoint: str
    timeout_seconds: int
    environment_id: UUID | None
    environment_revision: int | None
    environment_variables: dict[str, str]
    parameters: dict[str, Any]
    secret_references: list[SecretReference]
    execution_policy: ExecutionPolicy
    created_at: datetime

    @classmethod
    def from_record(cls, record: RunSpecRecord) -> "RunSpecResponse":
        return cls.model_validate(record, from_attributes=True)