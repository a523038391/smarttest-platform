from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from .environment_repository import EnvironmentRepository
from .environment_schemas import (
    EnvironmentCreate,
    EnvironmentListResponse,
    EnvironmentResponse,
    EnvironmentUpdate,
)
from .environment_sql_repository import SqlEnvironmentRepository


environment_router = APIRouter(
    prefix="/api/v1/environments", tags=["environments"]
)


def get_environment_repository(
    request: Request,
) -> EnvironmentRepository | SqlEnvironmentRepository:
    return request.app.state.environment_repository


@environment_router.post(
    "", response_model=EnvironmentResponse, status_code=201,
)
def create_environment(
    body: EnvironmentCreate,
    repository: EnvironmentRepository | SqlEnvironmentRepository = Depends(
        get_environment_repository
    ),
) -> EnvironmentResponse:
    return EnvironmentResponse.from_record(repository.create_environment(
        body.project_id, body.name,
        [item.to_input() for item in body.environment_variables],
        [item.to_input() for item in body.common_parameters],
    ))


@environment_router.get("", response_model=EnvironmentListResponse)
def list_environments(
    project_id: UUID = Query(),
    repository: EnvironmentRepository | SqlEnvironmentRepository = Depends(
        get_environment_repository
    ),
) -> EnvironmentListResponse:
    items = [
        EnvironmentResponse.from_record(item)
        for item in repository.list_environments(project_id)
    ]
    return EnvironmentListResponse(items=items, total=len(items))


@environment_router.get(
    "/{environment_id}/revisions", response_model=EnvironmentListResponse,
)
def list_environment_revisions(
    environment_id: UUID,
    repository: EnvironmentRepository | SqlEnvironmentRepository = Depends(
        get_environment_repository
    ),
) -> EnvironmentListResponse:
    items = [
        EnvironmentResponse.from_record(item)
        for item in repository.list_revisions(environment_id)
    ]
    return EnvironmentListResponse(items=items, total=len(items))


@environment_router.get(
    "/{environment_id}", response_model=EnvironmentResponse,
)
def get_environment(
    environment_id: UUID,
    repository: EnvironmentRepository | SqlEnvironmentRepository = Depends(
        get_environment_repository
    ),
) -> EnvironmentResponse:
    return EnvironmentResponse.from_record(repository.get_environment(environment_id))


@environment_router.put(
    "/{environment_id}", response_model=EnvironmentResponse,
)
def update_environment(
    environment_id: UUID,
    body: EnvironmentUpdate,
    repository: EnvironmentRepository | SqlEnvironmentRepository = Depends(
        get_environment_repository
    ),
) -> EnvironmentResponse:
    return EnvironmentResponse.from_record(repository.update_environment(
        environment_id, expected_version=body.state_version, name=body.name,
        status=body.status,
        environment_variables=[item.to_input() for item in body.environment_variables],
        common_parameters=[item.to_input() for item in body.common_parameters],
    ))