from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import Field

from .ai_cases import AiCaseGenerator, CaseCandidate, MAX_CASE_CANDIDATES
from .quality_domain import InvalidAssetTransition, RequirementStatus
from .quality_repository import QualityRepository
from .schemas import ApiModel

ai_router = APIRouter(prefix="/api/v1/ai", tags=["ai-design"])


class CaseGenerationCreate(ApiModel):
    requirement_id: UUID
    count: int = Field(default=5, ge=1, le=MAX_CASE_CANDIDATES)


class CaseGenerationResponse(ApiModel):
    requirement_id: UUID
    candidates: list[CaseCandidate]


def get_quality_repository(request: Request) -> QualityRepository:
    return request.app.state.quality_repository


def get_case_generator(request: Request) -> AiCaseGenerator:
    return request.app.state.case_generator


@ai_router.post("/case-generations", response_model=CaseGenerationResponse)
def generate_cases(
    body: CaseGenerationCreate,
    repository: QualityRepository = Depends(get_quality_repository),
    generator: AiCaseGenerator = Depends(get_case_generator),
) -> CaseGenerationResponse:
    requirement = repository.get_requirement(body.requirement_id)
    if requirement.status is RequirementStatus.ARCHIVED:
        raise InvalidAssetTransition(
            "cannot generate test cases for an archived requirement"
        )
    candidates = generator.generate(requirement, body.count)
    return CaseGenerationResponse(
        requirement_id=requirement.id, candidates=list(candidates)
    )