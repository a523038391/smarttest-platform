from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response, status

from .environment_repository import EnvironmentNotFound
from .load_test_repository import LoadTestRepository
from .load_test_schemas import (
    LoadTestCreate, LoadTestListResponse, LoadTestResponse,
    LoadTestRunListResponse, LoadTestRunResponse, LoadTestUpdate,
)
from .load_test_service import LoadTestService
from .load_test_sql_repository import SqlLoadTestRepository


load_test_router = APIRouter(prefix="/api/v1/load-tests", tags=["load-tests"])
load_test_run_router = APIRouter(prefix="/api/v1/load-test-runs", tags=["load-tests"])
LoadTestRepositoryLike = LoadTestRepository | SqlLoadTestRepository


def get_repository(request: Request) -> LoadTestRepositoryLike:
    return request.app.state.load_test_repository


def get_service(request: Request) -> LoadTestService:
    return request.app.state.load_test_service


def _values(body: LoadTestCreate | LoadTestUpdate) -> dict:
    return {
        "name": body.name, "description": body.description,
        "targets": [target.model_dump(mode="json") for target in body.targets],
        "mode": body.mode, "request_count": body.request_count,
        "duration_seconds": body.duration_seconds, "concurrency": body.concurrency,
        "interval_ms": body.interval_ms, "timeout_seconds": body.timeout_seconds,
        "environment_id": body.environment_id,
        "traffic_mode": body.traffic_mode,
        "initial_variables": body.initial_variables,
        "stop_on_failure": body.stop_on_failure,
    }


def _validate_environment(request: Request, project_id: UUID,
                          environment_id: UUID | None) -> None:
    if environment_id is None:
        return
    environment = request.app.state.environment_repository.get_environment(environment_id)
    if environment.project_id != project_id:
        raise EnvironmentNotFound(f"environment {environment_id} was not found")


@load_test_router.get("", response_model=LoadTestListResponse)
def list_load_tests(project_id: UUID = Query(),
                    repository: LoadTestRepositoryLike = Depends(get_repository)) -> LoadTestListResponse:
    items = [LoadTestResponse.from_record(item)
             for item in repository.list_load_tests(project_id)]
    return LoadTestListResponse(items=items, total=len(items))


@load_test_router.post("", response_model=LoadTestResponse, status_code=201)
def create_load_test(body: LoadTestCreate, request: Request,
                     repository: LoadTestRepositoryLike = Depends(get_repository)) -> LoadTestResponse:
    request.app.state.project_repository.get_project(body.project_id)
    _validate_environment(request, body.project_id, body.environment_id)
    return LoadTestResponse.from_record(repository.create_load_test(
        project_id=body.project_id, **_values(body)
    ))


@load_test_router.get("/{load_test_id}", response_model=LoadTestResponse)
def get_load_test(load_test_id: UUID,
                  repository: LoadTestRepositoryLike = Depends(get_repository)) -> LoadTestResponse:
    return LoadTestResponse.from_record(repository.get_load_test(load_test_id))


@load_test_router.put("/{load_test_id}", response_model=LoadTestResponse)
def update_load_test(load_test_id: UUID, body: LoadTestUpdate, request: Request,
                     repository: LoadTestRepositoryLike = Depends(get_repository)) -> LoadTestResponse:
    current = repository.get_load_test(load_test_id)
    _validate_environment(request, current.project_id, body.environment_id)
    return LoadTestResponse.from_record(repository.update_load_test(
        load_test_id, **_values(body), expected_version=body.state_version
    ))


@load_test_router.delete("/{load_test_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_load_test(load_test_id: UUID,
                     repository: LoadTestRepositoryLike = Depends(get_repository)) -> Response:
    repository.delete_load_test(load_test_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@load_test_router.post(
    "/{load_test_id}/runs", response_model=LoadTestRunResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def start_load_test(load_test_id: UUID,
                    service: LoadTestService = Depends(get_service)) -> LoadTestRunResponse:
    return LoadTestRunResponse.from_record(service.start_run(load_test_id))


@load_test_router.get("/{load_test_id}/runs", response_model=LoadTestRunListResponse)
def list_load_test_runs(load_test_id: UUID,
                        repository: LoadTestRepositoryLike = Depends(get_repository)) -> LoadTestRunListResponse:
    items = [LoadTestRunResponse.from_record(item) for item in repository.list_runs(load_test_id)]
    return LoadTestRunListResponse(items=items, total=len(items))


@load_test_run_router.get("/{run_id}", response_model=LoadTestRunResponse)
def get_load_test_run(run_id: UUID,
                      repository: LoadTestRepositoryLike = Depends(get_repository)) -> LoadTestRunResponse:
    return LoadTestRunResponse.from_record(repository.get_run(run_id))


@load_test_run_router.post(
    "/{run_id}/cancel", response_model=LoadTestRunResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def cancel_load_test_run(run_id: UUID,
                         service: LoadTestService = Depends(get_service)) -> LoadTestRunResponse:
    return LoadTestRunResponse.from_record(service.cancel_run(run_id))