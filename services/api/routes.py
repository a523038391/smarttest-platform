import asyncio
import json
from collections.abc import AsyncIterator
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse

from services.artifacts import ArtifactStore

from .dispatcher import DispatchUnavailable, RunDispatcher
from .domain import RUN_TERMINAL, EventRecord, RunState
from .repository import ArtifactNotFound, RunNotFound, RunRepository
from .schemas import (
    ArtifactListResponse,
    ArtifactResponse,
    AttemptListResponse,
    AttemptResponse,
    EventListResponse,
    EventResponse,
    HealthResponse,
    RunCreate,
    RunListResponse,
    RunResponse,
)

health_router = APIRouter(tags=["health"])
runs_router = APIRouter(prefix="/api/v1/runs", tags=["runs"])


def get_repository(request: Request) -> RunRepository:
    return request.app.state.run_repository


def get_artifact_store(request: Request) -> ArtifactStore:
    return request.app.state.artifact_store


@health_router.get("/health/live", response_model=HealthResponse)
def live() -> HealthResponse:
    return HealthResponse(status="ok")


@health_router.get("/health/ready", response_model=HealthResponse)
def ready(repository: RunRepository = Depends(get_repository)) -> HealthResponse:
    repository.list()
    return HealthResponse(status="ok")


@runs_router.post("", response_model=RunResponse, status_code=status.HTTP_201_CREATED)
def create_run(
    body: RunCreate,
    request: Request,
    response: Response,
    idempotency_key: str | None = Header(
        default=None, alias="Idempotency-Key", max_length=255
    ),
    repository: RunRepository = Depends(get_repository),
) -> RunResponse:
    if idempotency_key is not None and not idempotency_key.strip():
        raise RequestValidationError(
            [
                {
                    "type": "value_error",
                    "loc": ("header", "Idempotency-Key"),
                    "msg": "Idempotency-Key must not be blank",
                    "input": idempotency_key,
                }
            ]
        )
    run, replayed = repository.create_or_get(
        body.engine, body.parameters, idempotency_key
    )
    if replayed:
        response.status_code = status.HTTP_200_OK
    elif request.app.state.settings.auto_dispatch:
        run = repository.transition(run.id, RunState.QUEUED)
        dispatcher: RunDispatcher = request.app.state.run_dispatcher
        try:
            dispatcher.dispatch(run.id)
        except Exception as exc:
            repository.transition(run.id, RunState.INFRA_ERROR)
            raise DispatchUnavailable("run could not be dispatched") from exc
    return RunResponse.from_record(run)


@runs_router.get("", response_model=RunListResponse)
def list_runs(repository: RunRepository = Depends(get_repository)) -> RunListResponse:
    runs = [RunResponse.from_record(run) for run in repository.list()]
    return RunListResponse(items=runs, total=len(runs))


@runs_router.get("/{run_id}/attempts", response_model=AttemptListResponse)
def list_attempts(
    run_id: UUID, repository: RunRepository = Depends(get_repository)
) -> AttemptListResponse:
    attempts = [
        AttemptResponse.from_record(attempt)
        for attempt in repository.list_attempts(run_id)
    ]
    return AttemptListResponse(items=attempts, total=len(attempts))


@runs_router.get("/{run_id}/events", response_model=EventListResponse)
def list_events(
    run_id: UUID,
    after: int = Query(default=0, ge=0),
    repository: RunRepository = Depends(get_repository),
) -> EventListResponse:
    events = [
        EventResponse.from_record(event)
        for event in repository.list_events(run_id, after)
    ]
    next_cursor = events[-1].cursor if events else after
    return EventListResponse(
        items=events, total=len(events), next_cursor=next_cursor
    )


@runs_router.get("/{run_id}/artifacts", response_model=ArtifactListResponse)
def list_artifacts(
    run_id: UUID, repository: RunRepository = Depends(get_repository)
) -> ArtifactListResponse:
    artifacts = [
        ArtifactResponse.from_record(item) for item in repository.list_artifacts(run_id)
    ]
    return ArtifactListResponse(items=artifacts, total=len(artifacts))


@runs_router.get("/{run_id}/artifacts/{artifact_id}/content")
def download_artifact(
    run_id: UUID,
    artifact_id: UUID,
    repository: RunRepository = Depends(get_repository),
    store: ArtifactStore = Depends(get_artifact_store),
):
    artifact = repository.get_artifact(artifact_id)
    if artifact.run_id != run_id:
        raise ArtifactNotFound(f"artifact {artifact_id} was not found")
    path = store.local_path(artifact.storage_key)
    if path is not None:
        return FileResponse(path, media_type=artifact.content_type, filename=artifact.name)
    url = store.download_url(artifact.storage_key)
    if url is None:
        raise ArtifactNotFound(f"artifact {artifact_id} content was not found")
    return RedirectResponse(url, status_code=status.HTTP_307_TEMPORARY_REDIRECT)


def _format_sse(event: EventRecord) -> str:
    body = EventResponse.from_record(event).model_dump(mode="json")
    return (
        f"id: {event.cursor}\n"
        f"event: {event.type.value}\n"
        f"data: {json.dumps(body, separators=(',', ':'))}\n\n"
    )


async def _event_stream(
    request: Request,
    repository: RunRepository,
    run_id: UUID,
    after: int,
    *,
    poll_interval: float = 0.25,
    heartbeat_interval: float = 15.0,
) -> AsyncIterator[str]:
    cursor = after
    elapsed = 0.0
    while True:
        events = await asyncio.to_thread(repository.list_events, run_id, cursor)
        for event in events:
            cursor = event.cursor
            elapsed = 0.0
            yield _format_sse(event)
        try:
            run = await asyncio.to_thread(repository.get, run_id)
        except RunNotFound:
            return
        if run.state in RUN_TERMINAL:
            return
        if await request.is_disconnected():
            return
        await asyncio.sleep(poll_interval)
        elapsed += poll_interval
        if elapsed >= heartbeat_interval:
            elapsed = 0.0
            yield ": heartbeat\n\n"


@runs_router.get("/{run_id}/events/stream")
def stream_events(
    run_id: UUID,
    request: Request,
    after: int = Query(default=0, ge=0),
    last_event_id: int | None = Header(
        default=None, alias="Last-Event-ID", ge=0
    ),
    repository: RunRepository = Depends(get_repository),
) -> StreamingResponse:
    repository.get(run_id)
    cursor = max(after, last_event_id or 0)
    return StreamingResponse(
        _event_stream(request, repository, run_id, cursor),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@runs_router.get("/{run_id}", response_model=RunResponse)
def get_run(
    run_id: UUID, repository: RunRepository = Depends(get_repository)
) -> RunResponse:
    return RunResponse.from_record(repository.get(run_id))


@runs_router.post("/{run_id}/cancel", response_model=RunResponse)
def cancel_run(
    run_id: UUID, repository: RunRepository = Depends(get_repository)
) -> RunResponse:
    return RunResponse.from_record(repository.cancel(run_id))