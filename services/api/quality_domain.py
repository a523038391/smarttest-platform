from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import Enum
from uuid import UUID


class RequirementStatus(str, Enum):
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"


class TestCaseStatus(str, Enum):
    DRAFT = "DRAFT"
    READY = "READY"
    ARCHIVED = "ARCHIVED"


class TestCasePriority(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class TestCaseSource(str, Enum):
    MANUAL = "MANUAL"
    AI = "AI"


REQUIREMENT_TRANSITIONS = {
    RequirementStatus.DRAFT: {RequirementStatus.ACTIVE},
    RequirementStatus.ACTIVE: {RequirementStatus.ARCHIVED},
}
TEST_CASE_TRANSITIONS = {
    TestCaseStatus.DRAFT: {TestCaseStatus.READY},
    TestCaseStatus.READY: {TestCaseStatus.ARCHIVED},
}


class InvalidAssetTransition(ValueError):
    pass


def _next_status(current: Enum, target: Enum, rules: dict[Enum, set[Enum]]) -> Enum:
    if current == target:
        if current not in rules:
            raise InvalidAssetTransition(
                f"cannot modify an asset in terminal state {current.value}"
            )
        return current
    if target not in rules.get(current, set()):
        raise InvalidAssetTransition(
            f"cannot transition from {current.value} to {target.value}"
        )
    return target


@dataclass(frozen=True, slots=True)
class CaseStepRecord:
    order: int
    action: str
    expected_result: str


@dataclass(frozen=True, slots=True)
class RequirementRecord:
    id: UUID
    project_id: UUID
    title: str
    description: str
    status: RequirementStatus
    revision: int
    state_version: int
    created_at: datetime
    updated_at: datetime

    def update(
        self, title: str, description: str, status: RequirementStatus
    ) -> "RequirementRecord":
        next_status = _next_status(self.status, status, REQUIREMENT_TRANSITIONS)
        return replace(
            self,
            title=title,
            description=description,
            status=next_status,
            revision=self.revision + 1,
            state_version=self.state_version + 1,
            updated_at=datetime.now(timezone.utc),
        )


@dataclass(frozen=True, slots=True)
class TestCaseRecord:
    id: UUID
    project_id: UUID
    title: str
    description: str
    preconditions: str
    priority: TestCasePriority
    status: TestCaseStatus
    source: TestCaseSource
    steps: tuple[CaseStepRecord, ...]
    revision: int
    state_version: int
    created_at: datetime
    updated_at: datetime

    def update(
        self,
        title: str,
        description: str,
        preconditions: str,
        priority: TestCasePriority,
        status: TestCaseStatus,
        steps: tuple[CaseStepRecord, ...],
    ) -> "TestCaseRecord":
        next_status = _next_status(self.status, status, TEST_CASE_TRANSITIONS)
        return replace(
            self,
            title=title,
            description=description,
            preconditions=preconditions,
            priority=priority,
            status=next_status,
            steps=steps,
            revision=self.revision + 1,
            state_version=self.state_version + 1,
            updated_at=datetime.now(timezone.utc),
        )


@dataclass(frozen=True, slots=True)
class TraceLinkRecord:
    requirement_id: UUID
    test_case_id: UUID
    created_at: datetime