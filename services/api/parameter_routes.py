from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response, status

from .parameter_repository import ParameterRepository
from .parameter_schemas import (
    ParameterEnumCreate, ParameterEnumListResponse, ParameterEnumResponse,
    ParameterEnumUpdate, ScriptParameterReplacement, ScriptParameterResponse,
)
from .parameter_sql_repository import SqlParameterRepository


parameter_enum_router = APIRouter(
    prefix="/api/v1/parameter-enums", tags=["parameter-enums"]
)
script_parameter_router = APIRouter(
    prefix="/api/v1/automation-scripts", tags=["automation-scripts"]
)


def get_parameter_repository(
    request: Request,
) -> ParameterRepository | SqlParameterRepository:
    return request.app.state.parameter_repository


@parameter_enum_router.get("", response_model=ParameterEnumListResponse)
def list_enum_sets(
    project_id: UUID = Query(),
    repository: ParameterRepository | SqlParameterRepository = Depends(
        get_parameter_repository
    ),
) -> ParameterEnumListResponse:
    items = [
        ParameterEnumResponse.from_record(item)
        for item in repository.list_enum_sets(project_id)
    ]
    return ParameterEnumListResponse(items=items, total=len(items))


@parameter_enum_router.post(
    "", response_model=ParameterEnumResponse, status_code=status.HTTP_201_CREATED
)
def create_enum_set(
    body: ParameterEnumCreate,
    repository: ParameterRepository | SqlParameterRepository = Depends(
        get_parameter_repository
    ),
) -> ParameterEnumResponse:
    return ParameterEnumResponse.from_record(repository.create_enum_set(
        body.project_id, body.name, body.description,
        [item.to_domain() for item in body.options],
    ))


@parameter_enum_router.put("/{enum_set_id}", response_model=ParameterEnumResponse)
def update_enum_set(
    enum_set_id: UUID,
    body: ParameterEnumUpdate,
    repository: ParameterRepository | SqlParameterRepository = Depends(
        get_parameter_repository
    ),
) -> ParameterEnumResponse:
    return ParameterEnumResponse.from_record(repository.update_enum_set(
        enum_set_id, expected_version=body.state_version, name=body.name,
        description=body.description,
        options=[item.to_domain() for item in body.options],
    ))


@parameter_enum_router.delete("/{enum_set_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_enum_set(
    enum_set_id: UUID,
    repository: ParameterRepository | SqlParameterRepository = Depends(
        get_parameter_repository
    ),
) -> Response:
    repository.delete_enum_set(enum_set_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@script_parameter_router.get(
    "/{script_id}/parameters", response_model=list[ScriptParameterResponse]
)
def list_script_parameters(
    script_id: UUID,
    repository: ParameterRepository | SqlParameterRepository = Depends(
        get_parameter_repository
    ),
) -> list[ScriptParameterResponse]:
    return [
        ScriptParameterResponse.from_record(item)
        for item in repository.list_script_parameters(script_id)
    ]


@script_parameter_router.put(
    "/{script_id}/parameters", response_model=list[ScriptParameterResponse]
)
def replace_script_parameters(
    script_id: UUID,
    body: ScriptParameterReplacement,
    repository: ParameterRepository | SqlParameterRepository = Depends(
        get_parameter_repository
    ),
) -> list[ScriptParameterResponse]:
    records = repository.replace_script_parameters(
        script_id, [item.to_domain() for item in body.root]
    )
    return [ScriptParameterResponse.from_record(item) for item in records]