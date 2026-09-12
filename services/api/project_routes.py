from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response, status

from .project_domain import ProjectStatus
from .project_repository import ProjectInUse, ProjectRepository
from .project_schemas import (
    ProjectCreate,
    ProjectListResponse,
    ProjectResponse,
    ProjectUpdate,
)
from .project_sql_repository import SqlProjectRepository

project_router = APIRouter(prefix="/api/v1/projects", tags=["projects"])

ProjectRepositoryLike = ProjectRepository | SqlProjectRepository


def get_project_repository(request: Request) -> ProjectRepositoryLike:
    return request.app.state.project_repository


@project_router.post("", response_model=ProjectResponse, status_code=201)
def create_project(
    body: ProjectCreate,
    repository: ProjectRepositoryLike = Depends(get_project_repository),
) -> ProjectResponse:
    return ProjectResponse.from_record(
        repository.create_project(body.name, body.description)
    )


@project_router.get("", response_model=ProjectListResponse)
def list_projects(
    status: ProjectStatus | None = Query(default=None),
    repository: ProjectRepositoryLike = Depends(get_project_repository),
) -> ProjectListResponse:
    items = [
        ProjectResponse.from_record(item)
        for item in repository.list_projects(status)
    ]
    return ProjectListResponse(items=items, total=len(items))


@project_router.get("/{project_id}", response_model=ProjectResponse)
def get_project(
    project_id: UUID,
    repository: ProjectRepositoryLike = Depends(get_project_repository),
) -> ProjectResponse:
    return ProjectResponse.from_record(repository.get_project(project_id))


@project_router.put("/{project_id}", response_model=ProjectResponse)
def update_project(
    project_id: UUID,
    body: ProjectUpdate,
    repository: ProjectRepositoryLike = Depends(get_project_repository),
) -> ProjectResponse:
    return ProjectResponse.from_record(repository.update_project(
        project_id,
        name=body.name,
        description=body.description,
        status=body.status,
        expected_version=body.state_version,
    ))


@project_router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project(
    project_id: UUID,
    request: Request,
    repository: ProjectRepositoryLike = Depends(get_project_repository),
) -> Response:
    repository.get_project(project_id)
    project_content = (
        request.app.state.quality_repository.list_requirements(project_id),
        request.app.state.quality_repository.list_test_cases(project_id),
        request.app.state.automation_repository.list_scripts(project_id),
        request.app.state.parameter_repository.list_enum_sets(project_id),
        request.app.state.environment_repository.list_environments(project_id),
        request.app.state.test_plan_repository.list_plans(project_id),
    )
    if any(project_content):
        raise ProjectInUse(
            "project contains requirements, test cases, scripts, parameter enums, "
            "environments, or test plans"
        )
    repository.delete_project(project_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
