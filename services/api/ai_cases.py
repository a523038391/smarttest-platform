from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .quality_domain import RequirementRecord, TestCasePriority

MAX_CASE_CANDIDATES = 20


class CaseGenerationUnavailable(RuntimeError):
    pass


class CaseGenerationError(RuntimeError):
    pass


class CaseGenerationProvider(Protocol):
    def generate(self, requirement: RequirementRecord, count: int) -> str:
        """Return a JSON object containing a `cases` array."""


class CandidateStep(BaseModel):
    model_config = ConfigDict(extra="forbid")
    order: int = Field(ge=1, le=10_000)
    action: str = Field(min_length=1, max_length=10_000)
    expected_result: str = Field(min_length=1, max_length=10_000)


class CaseCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=255)
    description: str = Field(default="", max_length=100_000)
    preconditions: str = Field(default="", max_length=100_000)
    priority: TestCasePriority
    steps: list[CandidateStep] = Field(min_length=1, max_length=1_000)

    @model_validator(mode="after")
    def validate_step_order(self) -> "CaseCandidate":
        if [item.order for item in self.steps] != list(range(1, len(self.steps) + 1)):
            raise ValueError("candidate steps must use contiguous order starting at 1")
        return self


class CandidateBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cases: list[CaseCandidate] = Field(min_length=1, max_length=MAX_CASE_CANDIDATES)


class AiCaseGenerator:
    def __init__(
        self,
        provider: CaseGenerationProvider | None = None,
        maximum_response_bytes: int = 256 * 1024,
    ) -> None:
        if maximum_response_bytes <= 0:
            raise ValueError("maximum_response_bytes must be positive")
        self.provider = provider
        self.maximum_response_bytes = maximum_response_bytes

    def generate(
        self, requirement: RequirementRecord, count: int
    ) -> tuple[CaseCandidate, ...]:
        if not 1 <= count <= MAX_CASE_CANDIDATES:
            raise ValueError(
                f"count must be between 1 and {MAX_CASE_CANDIDATES}"
            )
        if self.provider is None:
            raise CaseGenerationUnavailable("AI case generation is not configured")
        try:
            raw = self.provider.generate(requirement, count)
        except CaseGenerationUnavailable:
            raise
        except Exception as exc:
            raise CaseGenerationError("AI provider request failed") from exc
        if not isinstance(raw, str) or len(raw.encode("utf-8")) > self.maximum_response_bytes:
            raise CaseGenerationError("AI provider response is invalid or too large")
        try:
            batch = CandidateBatch.model_validate_json(raw)
        except (ValidationError, ValueError) as exc:
            raise CaseGenerationError("AI provider returned invalid candidate data") from exc
        if len(batch.cases) > count:
            raise CaseGenerationError("AI provider returned too many candidates")
        return tuple(batch.cases)