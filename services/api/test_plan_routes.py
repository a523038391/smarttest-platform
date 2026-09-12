from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, Response, status
from fastapi.exceptions import RequestValidationError
from packages.protocol import Engine

from services.source_store import LocalSourceStore

from .dispatcher import DispatchUnavailable, RunDispatcher
from .execution_coordinator import ExecutionBatchCoordinator
from .project_domain import ProjectStatus
from .repository import RunRepository
from .test_plan_domain import TestPlanStatus
from .test_plan_repository import TestPlanConflict, TestPlanNotFound, TestPlanRepository
from .test_plan_schemas import (
    ExecutionBatchResponse, RunSpecResponse, TestPlanCreate, TestPlanExecutionCreate,
    TestPlanListResponse, TestPlanResponse, TestPlanUpdate,
)

test_plans_router = APIRouter(prefix="/api/v1/test-plans", tags=["test-plans"])
execution_batches_router = APIRouter(
    prefix="/api/v1/execution-batches", tags=["test-plans"]
)
run_specs_router = APIRouter(prefix="/api/v1/runs", tags=["test-plans"])


def get_test_plan_repository(request: Request) -> TestPlanRepository:
    return request.app.state.test_plan_repository


def _execution_source_override(
    plan_id: UUID, body: TestPlanExecutionCreate, request: Request,
    repository: TestPlanRepository,
) -> tuple[str, str] | None:
    plan = repository.get_plan(plan_id)
    scripts = [
        request.app.state.automation_repository.get_revision(
            item.script_id, item.script_revision
        )
        for item in plan.items
    ]
    if body.execution_target == "docker":
        if any(script.source_ref.startswith("host-source:") for script in scripts):
            raise TestPlanConflict(
                "docker execution cannot use legacy host-source scripts"
            )
        return None
    if plan.status is not TestPlanStatus.ACTIVE:
        raise TestPlanConflict("only an active test plan revision can be executed")
    project = request.app.state.project_repository.get_project(plan.project_id)
    if project.status is not ProjectStatus.ACTIVE:
        raise TestPlanConflict("host execution requires an active project")
    if any(script.engine is Engine.HTTP for script in scripts):
        raise TestPlanConflict("host execution does not support HTTP scripts")
    assert body.project_directory is not None and body.python_executable is not None
    store: LocalSourceStore = request.app.state.source_store
    source = store.register_host(
        plan.project_id, body.project_directory, body.python_executable
    )
    for script in scripts:
        store.resolve_host_reference(
            plan.project_id, source.source_ref, source.content_digest, script.entrypoint
        )
    return source.source_ref, source.content_digest


@test_plans_router.post("", response_model=TestPlanResponse, status_code=201)
def create_test_plan(
    body: TestPlanCreate,
    repository: TestPlanRepository = Depends(get_test_plan_repository),
) -> TestPlanResponse:
    return TestPlanResponse.from_record(repository.create_plan(
        body.tenant_id, body.project_id, body.name, body.description,
        body.records(), body.max_parallel,
    ))


@test_plans_router.get("", response_model=TestPlanListResponse)
def list_test_plans(
    project_id: UUID = Query(),
    repository: TestPlanRepository = Depends(get_test_plan_repository),
) -> TestPlanListResponse:
    items = [
        TestPlanResponse.from_record(item) for item in repository.list_plans(project_id)
    ]
    return TestPlanListResponse(items=items, total=len(items))


@test_plans_router.get("/{plan_id}/revisions", response_model=TestPlanListResponse)
def list_test_plan_revisions(
    plan_id: UUID,
    repository: TestPlanRepository = Depends(get_test_plan_repository),
) -> TestPlanListResponse:
    items = [
        TestPlanResponse.from_record(item) for item in repository.list_revisions(plan_id)
    ]
    return TestPlanListResponse(items=items, total=len(items))


@test_plans_router.get(
    "/{plan_id}/revisions/{revision}", response_model=TestPlanResponse
)
def get_test_plan_revision(
    plan_id: UUID, revision: int,
    repository: TestPlanRepository = Depends(get_test_plan_repository),
) -> TestPlanResponse:
    return TestPlanResponse.from_record(repository.get_revision(plan_id, revision))


@test_plans_router.get("/{plan_id}", response_model=TestPlanResponse)
def get_test_plan(
    plan_id: UUID,
    repository: TestPlanRepository = Depends(get_test_plan_repository),
) -> TestPlanResponse:
    return TestPlanResponse.from_record(repository.get_plan(plan_id))


@test_plans_router.put("/{plan_id}", response_model=TestPlanResponse)
def update_test_plan(
    plan_id: UUID, body: TestPlanUpdate,
    repository: TestPlanRepository = Depends(get_test_plan_repository),
) -> TestPlanResponse:
    return TestPlanResponse.from_record(repository.update_plan(
        plan_id, expected_version=body.state_version, name=body.name,
        description=body.description, items=body.records(), status=body.status,
        max_parallel=body.max_parallel,
    ))


@test_plans_router.post(
    "/{plan_id}/executions", response_model=ExecutionBatchResponse,
    status_code=status.HTTP_201_CREATED,
)
def execute_test_plan(
    plan_id: UUID, request: Request, response: Response,
    body: TestPlanExecutionCreate | None = None,
    idempotency_key: str = Header(alias="Idempotency-Key", max_length=255),
    repository: TestPlanRepository = Depends(get_test_plan_repository),
) -> ExecutionBatchResponse:
    if not idempotency_key.strip():
        raise RequestValidationError([{
            "type": "value_error", "loc": ("header", "Idempotency-Key"),
            "msg": "Idempotency-Key must not be blank", "input": idempotency_key,
        }])
    if body is None:
        batch, replayed = repository.execute(plan_id, idempotency_key)
    else:
        source_override = _execution_source_override(plan_id, body, request, repository)
        batch, replayed = repository.execute(
            plan_id, idempotency_key, source_override
        )
    if replayed:
        response.status_code = status.HTTP_200_OK
    elif request.app.state.settings.auto_dispatch:
        runs: RunRepository = request.app.state.run_repository
        dispatcher: RunDispatcher = request.app.state.run_dispatcher
        result = ExecutionBatchCoordinator(repository, runs, dispatcher).dispatch_available(
            batch.id
        )
        if result.failed:
            raise DispatchUnavailable("one or more plan runs could not be dispatched")
    return ExecutionBatchResponse.from_record(batch)


@test_plans_router.get(
    "/{plan_id}/executions/{batch_id}", response_model=ExecutionBatchResponse
)
def get_plan_execution_batch(
    plan_id: UUID, batch_id: UUID,
    repository: TestPlanRepository = Depends(get_test_plan_repository),
) -> ExecutionBatchResponse:
    batch = repository.get_execution_batch(batch_id)
    if batch.plan_id != plan_id:
        raise TestPlanNotFound(f"execution batch {batch_id} was not found")
    return ExecutionBatchResponse.from_record(batch)


@execution_batches_router.get("/{batch_id}", response_model=ExecutionBatchResponse)
def get_execution_batch(
    batch_id: UUID,
    repository: TestPlanRepository = Depends(get_test_plan_repository),
) -> ExecutionBatchResponse:
    return ExecutionBatchResponse.from_record(repository.get_execution_batch(batch_id))


@run_specs_router.get("/{run_id}/run-spec", response_model=RunSpecResponse)
def get_run_spec(
    run_id: UUID,
    repository: TestPlanRepository = Depends(get_test_plan_repository),
) -> RunSpecResponse:
    return RunSpecResponse.from_record(repository.get_run_spec(run_id))