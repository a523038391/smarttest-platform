from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response, status

from .data_factory_executor import DataFactoryExecutor
from .data_factory_repository import DataFactoryRepository
from .data_factory_schemas import (
    DataFactoryRunListResponse, DataFactoryRunResponse, DataFactoryWorkflowCreate,
    DataFactoryWorkflowListResponse, DataFactoryWorkflowResponse,
    DataFactoryWorkflowUpdate, WorkflowDebugSchema, WorkflowExecutionSchema,
)
from .data_factory_sql_repository import SqlDataFactoryRepository


data_factory_router = APIRouter(
    prefix="/api/v1/data-factory/workflows", tags=["data-factory"]
)
DataFactoryRepositoryLike = DataFactoryRepository | SqlDataFactoryRepository


def get_data_factory_repository(request: Request) -> DataFactoryRepositoryLike:
    return request.app.state.data_factory_repository


def get_data_factory_executor(request: Request) -> DataFactoryExecutor:
    return request.app.state.data_factory_executor


@data_factory_router.get("", response_model=DataFactoryWorkflowListResponse)
def list_workflows(
    project_id: UUID = Query(),
    repository: DataFactoryRepositoryLike = Depends(get_data_factory_repository),
) -> DataFactoryWorkflowListResponse:
    items = [DataFactoryWorkflowResponse.from_record(item)
             for item in repository.list_workflows(project_id)]
    return DataFactoryWorkflowListResponse(items=items, total=len(items))


@data_factory_router.post(
    "", response_model=DataFactoryWorkflowResponse, status_code=status.HTTP_201_CREATED,
)
def create_workflow(
    body: DataFactoryWorkflowCreate,
    request: Request,
    repository: DataFactoryRepositoryLike = Depends(get_data_factory_repository),
) -> DataFactoryWorkflowResponse:
    request.app.state.project_repository.get_project(body.project_id)
    return DataFactoryWorkflowResponse.from_record(repository.create_workflow(
        body.project_id, body.name, body.description, body.nodes, body.edges, body.variables,
    ))


@data_factory_router.get("/{workflow_id}", response_model=DataFactoryWorkflowResponse)
def get_workflow(
    workflow_id: UUID,
    repository: DataFactoryRepositoryLike = Depends(get_data_factory_repository),
) -> DataFactoryWorkflowResponse:
    return DataFactoryWorkflowResponse.from_record(repository.get_workflow(workflow_id))


@data_factory_router.put("/{workflow_id}", response_model=DataFactoryWorkflowResponse)
def update_workflow(
    workflow_id: UUID,
    body: DataFactoryWorkflowUpdate,
    repository: DataFactoryRepositoryLike = Depends(get_data_factory_repository),
) -> DataFactoryWorkflowResponse:
    return DataFactoryWorkflowResponse.from_record(repository.update_workflow(
        workflow_id, project_id=body.project_id, name=body.name,
        description=body.description, nodes=body.nodes, edges=body.edges,
        variables=body.variables, expected_version=body.state_version,
    ))


@data_factory_router.delete("/{workflow_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_workflow(
    workflow_id: UUID,
    repository: DataFactoryRepositoryLike = Depends(get_data_factory_repository),
) -> Response:
    repository.delete_workflow(workflow_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@data_factory_router.post(
    "/{workflow_id}/execute", response_model=DataFactoryRunResponse,
)
def execute_workflow(
    workflow_id: UUID,
    body: WorkflowExecutionSchema,
    repository: DataFactoryRepositoryLike = Depends(get_data_factory_repository),
    executor: DataFactoryExecutor = Depends(get_data_factory_executor),
) -> DataFactoryRunResponse:
    workflow = repository.get_workflow(workflow_id)
    run = executor.execute(
        workflow.definition(), body.variables, workflow_id=str(workflow_id)
    )
    repository.save_run(workflow_id, run)
    return DataFactoryRunResponse.from_record(run)


@data_factory_router.post(
    "/{workflow_id}/debug", response_model=DataFactoryRunResponse,
)
def debug_workflow(
    workflow_id: UUID,
    body: WorkflowDebugSchema,
    repository: DataFactoryRepositoryLike = Depends(get_data_factory_repository),
    executor: DataFactoryExecutor = Depends(get_data_factory_executor),
) -> DataFactoryRunResponse:
    workflow = repository.get_workflow(workflow_id)
    run = executor.debug(
        workflow.definition(), body.node_id, body.variables,
        workflow_id=str(workflow_id),
    )
    return DataFactoryRunResponse.from_record(run)


@data_factory_router.get(
    "/{workflow_id}/runs", response_model=DataFactoryRunListResponse,
)
def list_workflow_runs(
    workflow_id: UUID,
    repository: DataFactoryRepositoryLike = Depends(get_data_factory_repository),
) -> DataFactoryRunListResponse:
    items = [DataFactoryRunResponse.from_record(item)
             for item in repository.list_runs(workflow_id)]
    return DataFactoryRunListResponse(items=items, total=len(items))