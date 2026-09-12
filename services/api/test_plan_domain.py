from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import UUID

from packages.protocol import Engine, ExecutionPolicy, SecretReference


class TestPlanStatus(str, Enum):
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"


class InvalidTestPlanTransition(ValueError):
    pass


PLAN_TRANSITIONS = {
    TestPlanStatus.DRAFT: {TestPlanStatus.ACTIVE},
    TestPlanStatus.ACTIVE: {TestPlanStatus.ARCHIVED},
}


def validate_plan_transition(current: TestPlanStatus, target: TestPlanStatus) -> None:
    if current is TestPlanStatus.ARCHIVED:
        raise InvalidTestPlanTransition("an archived test plan cannot be modified")
    if target != current and target not in PLAN_TRANSITIONS[current]:
        raise InvalidTestPlanTransition(
            f"cannot transition test plan from {current.value} to {target.value}"
        )


@dataclass(frozen=True, slots=True)
class TestPlanDataRow:
    row_key: str
    values: dict[str, Any] = field(repr=False)


@dataclass(frozen=True, slots=True)
class TestPlanItem:
    item_id: UUID
    script_id: UUID
    script_revision: int
    environment_id: UUID | None
    environment_revision: int | None
    default_parameters: dict[str, Any] = field(repr=False)
    data_rows: tuple[TestPlanDataRow, ...] = field(repr=False)
    execution_policy: ExecutionPolicy = field(default_factory=ExecutionPolicy, repr=False)


@dataclass(frozen=True, slots=True)
class TestPlanRecord:
    id: UUID
    tenant_id: UUID
    project_id: UUID
    name: str
    description: str
    items: tuple[TestPlanItem, ...] = field(repr=False)
    max_parallel: int
    status: TestPlanStatus
    revision: int
    state_version: int
    created_at: datetime
    updated_at: datetime

    def update(
        self, *, name: str, description: str, items: tuple[TestPlanItem, ...],
        max_parallel: int, status: TestPlanStatus,
    ) -> "TestPlanRecord":
        validate_plan_transition(self.status, status)
        return replace(
            self, name=name, description=description, items=items,
            max_parallel=max_parallel, status=status,
            revision=self.revision + 1, state_version=self.state_version + 1,
            updated_at=datetime.now(timezone.utc),
        )


@dataclass(frozen=True, slots=True)
class RunSpecRecord:
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
    environment_variables: dict[str, str] = field(repr=False)
    parameters: dict[str, Any] = field(repr=False)
    secret_references: tuple[SecretReference, ...] = field(repr=False)
    execution_policy: ExecutionPolicy = field(repr=False)
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ExecutionBatchRecord:
    id: UUID
    tenant_id: UUID
    project_id: UUID
    plan_id: UUID
    plan_revision: int
    idempotency_key: str
    max_parallel: int
    run_ids: tuple[UUID, ...]
    created_at: datetime