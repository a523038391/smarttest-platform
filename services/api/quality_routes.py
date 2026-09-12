from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response, status

from .quality_repository import QualityRepository
from .quality_schemas import (
    RequirementCreate, RequirementListResponse, RequirementResponse,
    RequirementUpdate, TestCaseCreate, TestCaseListResponse, TestCaseResponse,
    TestCaseUpdate, TraceLinkCreate, TraceLinkResponse,
)

requirements_router = APIRouter(prefix="/api/v1/requirements", tags=["requirements"])
test_cases_router = APIRouter(prefix="/api/v1/test-cases", tags=["test-cases"])


def get_quality_repository(request: Request) -> QualityRepository:
    return request.app.state.quality_repository


@requirements_router.post("", response_model=RequirementResponse, status_code=201)
def create_requirement(
    body: RequirementCreate,
    repository: QualityRepository = Depends(get_quality_repository),
) -> RequirementResponse:
    return RequirementResponse.from_record(repository.create_requirement(
        body.project_id, body.title, body.description
    ))


@requirements_router.get("", response_model=RequirementListResponse)
def list_requirements(
    project_id: UUID = Query(),
    repository: QualityRepository = Depends(get_quality_repository),
) -> RequirementListResponse:
    items = [
        RequirementResponse.from_record(item)
        for item in repository.list_requirements(project_id)
    ]
    return RequirementListResponse(items=items, total=len(items))


@requirements_router.get("/{requirement_id}/revisions", response_model=RequirementListResponse)
def list_requirement_revisions(
    requirement_id: UUID,
    repository: QualityRepository = Depends(get_quality_repository),
) -> RequirementListResponse:
    items = [RequirementResponse.from_record(item) for item in
             repository.list_requirement_revisions(requirement_id)]
    return RequirementListResponse(items=items, total=len(items))


@requirements_router.get("/{requirement_id}/test-cases", response_model=TestCaseListResponse)
def list_requirement_test_cases(
    requirement_id: UUID,
    repository: QualityRepository = Depends(get_quality_repository),
) -> TestCaseListResponse:
    items = [TestCaseResponse.from_record(item) for item in
             repository.list_requirement_test_cases(requirement_id)]
    return TestCaseListResponse(items=items, total=len(items))


@requirements_router.post(
    "/{requirement_id}/test-cases", response_model=TraceLinkResponse,
    status_code=status.HTTP_201_CREATED,
)
def link_requirement_test_case(
    requirement_id: UUID,
    body: TraceLinkCreate,
    response: Response,
    repository: QualityRepository = Depends(get_quality_repository),
) -> TraceLinkResponse:
    link, replayed = repository.link_requirement_test_case(
        requirement_id, body.test_case_id
    )
    if replayed:
        response.status_code = status.HTTP_200_OK
    return TraceLinkResponse.from_record(link)


@requirements_router.get("/{requirement_id}", response_model=RequirementResponse)
def get_requirement(
    requirement_id: UUID,
    repository: QualityRepository = Depends(get_quality_repository),
) -> RequirementResponse:
    return RequirementResponse.from_record(repository.get_requirement(requirement_id))


@requirements_router.put("/{requirement_id}", response_model=RequirementResponse)
def update_requirement(
    requirement_id: UUID,
    body: RequirementUpdate,
    repository: QualityRepository = Depends(get_quality_repository),
) -> RequirementResponse:
    return RequirementResponse.from_record(repository.update_requirement(
        requirement_id, body.title, body.description, body.status, body.state_version
    ))


@test_cases_router.post("", response_model=TestCaseResponse, status_code=201)
def create_test_case(
    body: TestCaseCreate,
    repository: QualityRepository = Depends(get_quality_repository),
) -> TestCaseResponse:
    return TestCaseResponse.from_record(repository.create_test_case(
        body.project_id, body.title, body.description, body.preconditions,
        body.priority, tuple(step.to_record() for step in body.steps),
    ))


@test_cases_router.get("", response_model=TestCaseListResponse)
def list_test_cases(
    project_id: UUID = Query(),
    repository: QualityRepository = Depends(get_quality_repository),
) -> TestCaseListResponse:
    items = [TestCaseResponse.from_record(item) for item in
             repository.list_test_cases(project_id)]
    return TestCaseListResponse(items=items, total=len(items))


@test_cases_router.get("/{test_case_id}/revisions", response_model=TestCaseListResponse)
def list_test_case_revisions(
    test_case_id: UUID,
    repository: QualityRepository = Depends(get_quality_repository),
) -> TestCaseListResponse:
    items = [TestCaseResponse.from_record(item) for item in
             repository.list_test_case_revisions(test_case_id)]
    return TestCaseListResponse(items=items, total=len(items))


@test_cases_router.get("/{test_case_id}", response_model=TestCaseResponse)
def get_test_case(
    test_case_id: UUID,
    repository: QualityRepository = Depends(get_quality_repository),
) -> TestCaseResponse:
    return TestCaseResponse.from_record(repository.get_test_case(test_case_id))


@test_cases_router.put("/{test_case_id}", response_model=TestCaseResponse)
def update_test_case(
    test_case_id: UUID,
    body: TestCaseUpdate,
    repository: QualityRepository = Depends(get_quality_repository),
) -> TestCaseResponse:
    return TestCaseResponse.from_record(repository.update_test_case(
        test_case_id,
        expected_version=body.state_version,
        title=body.title,
        description=body.description,
        preconditions=body.preconditions,
        priority=body.priority,
        status=body.status,
        steps=tuple(step.to_record() for step in body.steps),
    ))