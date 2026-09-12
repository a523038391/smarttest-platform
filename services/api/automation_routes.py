from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response, status
from packages.protocol import Engine
from starlette.concurrency import run_in_threadpool
from services.source_store import LocalSourceStore, MAX_ARCHIVE_BYTES, SourceStoreError

from .automation_repository import AutomationRepository
from .automation_schemas import (
    AutomationGitSourceCreate,
    AutomationHostSourceCreate,
    AutomationScriptCreate,
    AutomationScriptListResponse,
    AutomationScriptResponse,
    AutomationScriptUpdate,
    AutomationSourceCreate,
    AutomationSourceResponse,
    TestCaseScriptLinkCreate,
    TestCaseScriptLinkResponse,
)
from .project_domain import ProjectStatus
from .project_routes import ProjectRepositoryLike


automation_router = APIRouter(
    prefix="/api/v1/automation-scripts", tags=["automation-scripts"]
)
automation_sources_router = APIRouter(
    prefix="/api/v1/automation-sources", tags=["automation-scripts"]
)
case_scripts_router = APIRouter(
    prefix="/api/v1/test-cases", tags=["automation-scripts"]
)


def get_automation_repository(request: Request) -> AutomationRepository:
    return request.app.state.automation_repository


def get_source_store(request: Request) -> LocalSourceStore:
    return request.app.state.source_store


def _validate_script_source(
    store: LocalSourceStore,
    project_id: UUID,
    source_ref: str,
    content_digest: str,
    entrypoint: str,
    engine: Engine,
) -> None:
    if source_ref.startswith("host-source:"):
        if engine is Engine.HTTP:
            raise SourceStoreError("HTTP scripts cannot use Windows host execution")
        store.validate_reference(project_id, source_ref, content_digest, entrypoint)
    elif source_ref.startswith("local-source:"):
        store.validate_reference(project_id, source_ref, content_digest, entrypoint)


@automation_sources_router.post(
    "", response_model=AutomationSourceResponse, status_code=status.HTTP_201_CREATED
)
def create_source(
    body: AutomationSourceCreate,
    store: LocalSourceStore = Depends(get_source_store),
) -> AutomationSourceResponse:
    return AutomationSourceResponse.from_record(store.put(body.project_id, body.content))


@automation_sources_router.post(
    "/zip", response_model=AutomationSourceResponse, status_code=status.HTTP_201_CREATED
)
async def create_zip_source(
    request: Request,
    project_id: UUID = Query(),
    store: LocalSourceStore = Depends(get_source_store),
) -> AutomationSourceResponse:
    if request.headers.get("content-type", "").split(";", 1)[0].lower() != "application/zip":
        raise SourceStoreError("ZIP upload must use the application/zip content type")
    content = bytearray()
    async for chunk in request.stream():
        if len(chunk) > MAX_ARCHIVE_BYTES - len(content):
            raise SourceStoreError("project ZIP exceeds the 10 MiB limit")
        content.extend(chunk)
    record = await run_in_threadpool(store.put_archive, project_id, bytes(content))
    return AutomationSourceResponse.from_record(record)


@automation_sources_router.post(
    "/git", response_model=AutomationSourceResponse, status_code=status.HTTP_201_CREATED
)
def create_git_source(
    body: AutomationGitSourceCreate,
    store: LocalSourceStore = Depends(get_source_store),
) -> AutomationSourceResponse:
    return AutomationSourceResponse.from_record(
        store.import_git(body.project_id, body.repository_url, body.git_ref)
    )


@automation_sources_router.post(
    "/host", response_model=AutomationSourceResponse, status_code=status.HTTP_201_CREATED
)
def create_host_source(
    body: AutomationHostSourceCreate,
    request: Request,
    store: LocalSourceStore = Depends(get_source_store),
) -> AutomationSourceResponse:
    projects: ProjectRepositoryLike = request.app.state.project_repository
    project = projects.get_project(body.project_id)
    if project.status is not ProjectStatus.ACTIVE:
        raise SourceStoreError("host sources require an active project")
    return AutomationSourceResponse.from_record(store.register_host(
        body.project_id, body.project_directory, body.python_executable
    ))


@automation_router.post("", response_model=AutomationScriptResponse, status_code=201)
def create_script(
    body: AutomationScriptCreate,
    repository: AutomationRepository = Depends(get_automation_repository),
    store: LocalSourceStore = Depends(get_source_store),
) -> AutomationScriptResponse:
    _validate_script_source(
        store, body.project_id, body.source_ref, body.content_digest,
        body.entrypoint, body.engine,
    )
    return AutomationScriptResponse.from_record(repository.create_script(
        body.project_id, body.name, body.description, body.engine,
        body.entrypoint, body.source_ref, body.content_digest,
        body.timeout_seconds,
    ))


@automation_router.get("", response_model=AutomationScriptListResponse)
def list_scripts(
    project_id: UUID = Query(),
    repository: AutomationRepository = Depends(get_automation_repository),
) -> AutomationScriptListResponse:
    items = [
        AutomationScriptResponse.from_record(item)
        for item in repository.list_scripts(project_id)
    ]
    return AutomationScriptListResponse(items=items, total=len(items))


@automation_router.get(
    "/{script_id}/revisions", response_model=AutomationScriptListResponse
)
def list_script_revisions(
    script_id: UUID,
    repository: AutomationRepository = Depends(get_automation_repository),
) -> AutomationScriptListResponse:
    items = [
        AutomationScriptResponse.from_record(item)
        for item in repository.list_revisions(script_id)
    ]
    return AutomationScriptListResponse(items=items, total=len(items))


@automation_router.get("/{script_id}", response_model=AutomationScriptResponse)
def get_script(
    script_id: UUID,
    repository: AutomationRepository = Depends(get_automation_repository),
) -> AutomationScriptResponse:
    return AutomationScriptResponse.from_record(repository.get_script(script_id))


@automation_router.put("/{script_id}", response_model=AutomationScriptResponse)
def update_script(
    script_id: UUID,
    body: AutomationScriptUpdate,
    repository: AutomationRepository = Depends(get_automation_repository),
    store: LocalSourceStore = Depends(get_source_store),
) -> AutomationScriptResponse:
    current = repository.get_script(script_id)
    _validate_script_source(
        store, current.project_id, body.source_ref, body.content_digest,
        body.entrypoint, current.engine,
    )
    return AutomationScriptResponse.from_record(repository.update_script(
        script_id,
        expected_version=body.state_version,
        name=body.name,
        description=body.description,
        entrypoint=body.entrypoint,
        source_ref=body.source_ref,
        content_digest=body.content_digest,
        timeout_seconds=body.timeout_seconds,
        status=body.status,
    ))


@case_scripts_router.post(
    "/{test_case_id}/automation-scripts",
    response_model=TestCaseScriptLinkResponse,
    status_code=status.HTTP_201_CREATED,
)
def link_test_case_script(
    test_case_id: UUID,
    body: TestCaseScriptLinkCreate,
    response: Response,
    repository: AutomationRepository = Depends(get_automation_repository),
) -> TestCaseScriptLinkResponse:
    link, replayed = repository.link_test_case(test_case_id, body.script_id)
    if replayed:
        response.status_code = status.HTTP_200_OK
    return TestCaseScriptLinkResponse.from_record(link)


@case_scripts_router.get(
    "/{test_case_id}/automation-scripts",
    response_model=AutomationScriptListResponse,
)
def list_test_case_scripts(
    test_case_id: UUID,
    repository: AutomationRepository = Depends(get_automation_repository),
) -> AutomationScriptListResponse:
    items = [
        AutomationScriptResponse.from_record(item)
        for item in repository.list_test_case_scripts(test_case_id)
    ]
    return AutomationScriptListResponse(items=items, total=len(items))