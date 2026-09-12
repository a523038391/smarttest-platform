from datetime import datetime
from uuid import UUID

from pydantic import Field, model_validator

from .quality_domain import (
    CaseStepRecord, RequirementRecord, RequirementStatus, TestCasePriority,
    TestCaseRecord, TestCaseSource, TestCaseStatus, TraceLinkRecord,
)
from .schemas import ApiModel


class RequirementCreate(ApiModel):
    project_id: UUID
    title: str = Field(min_length=1, max_length=255)
    description: str = Field(default="", max_length=100_000)


class RequirementUpdate(ApiModel):
    title: str = Field(min_length=1, max_length=255)
    description: str = Field(default="", max_length=100_000)
    status: RequirementStatus
    state_version: int = Field(ge=0)


class RequirementResponse(ApiModel):
    id: UUID
    project_id: UUID
    title: str
    description: str
    status: RequirementStatus
    revision: int
    state_version: int
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_record(cls, record: RequirementRecord) -> "RequirementResponse":
        return cls.model_validate(record, from_attributes=True)


class RequirementListResponse(ApiModel):
    items: list[RequirementResponse]
    total: int


class CaseStep(ApiModel):
    order: int = Field(ge=1, le=10_000)
    action: str = Field(min_length=1, max_length=10_000)
    expected_result: str = Field(min_length=1, max_length=10_000)

    def to_record(self) -> CaseStepRecord:
        return CaseStepRecord(self.order, self.action, self.expected_result)


class _TestCaseBody(ApiModel):
    title: str = Field(min_length=1, max_length=255)
    description: str = Field(default="", max_length=100_000)
    preconditions: str = Field(default="", max_length=100_000)
    priority: TestCasePriority = TestCasePriority.MEDIUM
    steps: list[CaseStep] = Field(min_length=1, max_length=1_000)

    @model_validator(mode="after")
    def validate_step_order(self) -> "_TestCaseBody":
        if [step.order for step in self.steps] != list(range(1, len(self.steps) + 1)):
            raise ValueError("steps must use contiguous order starting at 1")
        return self


class TestCaseCreate(_TestCaseBody):
    project_id: UUID


class TestCaseUpdate(_TestCaseBody):
    status: TestCaseStatus
    state_version: int = Field(ge=0)


class TestCaseResponse(ApiModel):
    id: UUID
    project_id: UUID
    title: str
    description: str
    preconditions: str
    priority: TestCasePriority
    status: TestCaseStatus
    source: TestCaseSource
    steps: list[CaseStep]
    revision: int
    state_version: int
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_record(cls, record: TestCaseRecord) -> "TestCaseResponse":
        return cls.model_validate(record, from_attributes=True)


class TestCaseListResponse(ApiModel):
    items: list[TestCaseResponse]
    total: int


class TraceLinkCreate(ApiModel):
    test_case_id: UUID


class TraceLinkResponse(ApiModel):
    requirement_id: UUID
    test_case_id: UUID
    created_at: datetime

    @classmethod
    def from_record(cls, record: TraceLinkRecord) -> "TraceLinkResponse":
        return cls.model_validate(record, from_attributes=True)