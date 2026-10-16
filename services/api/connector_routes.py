import time
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response

from .connector_client import ConnectorClient, ConnectorConnectionError
from .connector_repository import ConnectorRepository, ConnectorVersionConflict
from .connector_schemas import (
    ConnectorCreate, ConnectorEnabledUpdate, ConnectorListResponse,
    ConnectorResponse, ConnectorTestResponse, ConnectorUpdate,
)
from .connector_sql_repository import SqlConnectorRepository


connector_router = APIRouter(prefix="/api/v1/connectors", tags=["connectors"])
ConnectorRepositoryLike = ConnectorRepository | SqlConnectorRepository


def get_connector_repository(request: Request) -> ConnectorRepositoryLike:
    return request.app.state.connector_repository


def get_connector_client(request: Request) -> ConnectorClient:
    return request.app.state.connector_client


@connector_router.post("", response_model=ConnectorResponse, status_code=201)
def create_connector(
    body: ConnectorCreate,
    repository: ConnectorRepositoryLike = Depends(get_connector_repository),
) -> ConnectorResponse:
    return ConnectorResponse.from_record(repository.create_connector(
        name=body.name, description=body.description, kind=body.kind,
        base_url=body.base_url, auth_type=body.auth_type,
        auth_header=body.auth_header, credential=body.credential,
        enabled=body.enabled, timeout_seconds=body.timeout_seconds,
    ))


@connector_router.get("", response_model=ConnectorListResponse)
def list_connectors(
    repository: ConnectorRepositoryLike = Depends(get_connector_repository),
) -> ConnectorListResponse:
    items = [ConnectorResponse.from_record(item) for item in repository.list_connectors()]
    return ConnectorListResponse(items=items, total=len(items))


@connector_router.get("/{connector_id}", response_model=ConnectorResponse)
def get_connector(
    connector_id: UUID,
    repository: ConnectorRepositoryLike = Depends(get_connector_repository),
) -> ConnectorResponse:
    return ConnectorResponse.from_record(repository.get_connector(connector_id))


@connector_router.put("/{connector_id}", response_model=ConnectorResponse)
def update_connector(
    connector_id: UUID, body: ConnectorUpdate,
    repository: ConnectorRepositoryLike = Depends(get_connector_repository),
) -> ConnectorResponse:
    return ConnectorResponse.from_record(repository.update_connector(
        connector_id, expected_version=body.state_version, name=body.name,
        description=body.description, kind=body.kind, base_url=body.base_url,
        auth_type=body.auth_type, auth_header=body.auth_header,
        credential=body.credential,
        credential_provided="credential" in body.model_fields_set,
        enabled=body.enabled, timeout_seconds=body.timeout_seconds,
    ))


@connector_router.patch("/{connector_id}/enabled", response_model=ConnectorResponse)
def set_connector_enabled(
    connector_id: UUID, body: ConnectorEnabledUpdate,
    repository: ConnectorRepositoryLike = Depends(get_connector_repository),
) -> ConnectorResponse:
    return ConnectorResponse.from_record(repository.set_enabled(
        connector_id, enabled=body.enabled, expected_version=body.state_version,
    ))


@connector_router.delete("/{connector_id}", status_code=204)
def delete_connector(
    connector_id: UUID,
    repository: ConnectorRepositoryLike = Depends(get_connector_repository),
) -> Response:
    repository.delete_connector(connector_id)
    return Response(status_code=204)


@connector_router.post("/{connector_id}/test", response_model=ConnectorTestResponse)
def test_connector(
    connector_id: UUID,
    repository: ConnectorRepositoryLike = Depends(get_connector_repository),
    client: ConnectorClient = Depends(get_connector_client),
) -> ConnectorTestResponse:
    record = repository.get_connector(connector_id)
    credential = repository.credential_for(record)
    started = time.monotonic()
    try:
        message, latency_ms, tools = client.test(record, credential)
        success, error = True, None
    except ConnectorConnectionError as exc:
        success, message, tools = False, str(exc), record.tools
        error = message
        latency_ms = round((time.monotonic() - started) * 1000, 3)
    try:
        updated = repository.record_test(
            connector_id, expected_version=record.state_version, success=success,
            tools=tools, error=error,
        )
    except ConnectorVersionConflict:
        updated = repository.get_connector(connector_id)
        message = (
            f"{message}. Test result was not saved because the connector "
            "configuration changed during the test."
        )
    return ConnectorTestResponse(
        connector=ConnectorResponse.from_record(updated), success=success,
        message=message, latency_ms=latency_ms,
        tool_count=len(tools),
    )