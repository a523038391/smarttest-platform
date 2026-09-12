from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from http import HTTPStatus

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from services.artifacts import ArtifactStore, LocalArtifactStore
from services.local_runner_service import (
    ExecutorFactory, HostExecutorFactory, LocalRunnerService,
)
from services.source_store import LocalSourceStore, SourceStoreError

from .auth_domain import session_token_digest
from .auth_repository import (
    AuthAlreadyInitialized,
    AuthRepository,
    AuthRepositoryLike,
    InvalidCredentials,
    UsernameConflict,
)
from .auth_routes import SESSION_COOKIE_NAME, auth_router
from .auth_sql_repository import SqlAuthRepository
from .auth_throttle import LoginRateLimited, LoginThrottle
from .automation_repository import (
    AutomationRepository,
    ScriptLinkConflict,
    ScriptNotFound,
)
from .automation_routes import (
    automation_router,
    automation_sources_router,
    case_scripts_router,
)
from .automation_sql_repository import SqlAutomationRepository
from .ai_cases import AiCaseGenerator, CaseGenerationError, CaseGenerationUnavailable
from .ai_routes import ai_router
from .config import Settings
from .database import create_database_engine, create_session_factory
from .dispatcher import (
    CeleryRunDispatcher,
    DatabaseRunDispatcher,
    DispatchUnavailable,
    RunDispatcher,
)
from .domain import InvalidTransition
from .environment_crypto import (
    SecretDecryptionError,
    SecretEncryptionUnavailable,
    SecretEncryptor,
)
from .environment_domain import InvalidEnvironmentTransition
from .environment_repository import (
    ConfigurationResolver,
    EnvironmentConflict,
    EnvironmentNotFound,
    EnvironmentRepository,
    EnvironmentVersionConflict,
)
from .environment_routes import environment_router
from .environment_sql_repository import SqlEnvironmentRepository
from .parameter_repository import (
    ParameterEnumConflict,
    ParameterEnumNotFound,
    ParameterEnumVersionConflict,
    ParameterRepository,
    ScriptParameterConflict,
)
from .parameter_routes import parameter_enum_router, script_parameter_router
from .parameter_sql_repository import SqlParameterRepository
from .project_domain import InvalidProjectTransition
from .project_repository import (
    ProjectInUse,
    ProjectNameConflict,
    ProjectNotFound,
    ProjectRepository,
    ProjectVersionConflict,
)
from .project_routes import project_router
from .project_sql_repository import SqlProjectRepository
from .quality_domain import InvalidAssetTransition
from .quality_repository import (
    AssetVersionConflict,
    QualityRepository,
    RequirementNotFound,
    TestCaseNotFound,
    TraceLinkConflict,
)
from .quality_routes import requirements_router, test_cases_router
from .quality_sql_repository import SqlQualityRepository
from .repository import (
    AttemptConflict,
    AttemptNotFound,
    ArtifactConflict,
    ArtifactNotFound,
    EventConflict,
    IdempotencyConflict,
    RunNotFound,
    RunRepository,
    TerminalRunConflict,
)
from .routes import health_router, runs_router
from .schemas import Problem, ValidationErrorDetail
from .service_control_routes import service_control_router
from .service_control_service import (
    ServiceControlForbidden,
    ServiceControlService,
    ServiceControlUnavailable,
)
from .sql_repository import SqlRunRepository
from .test_plan_domain import InvalidTestPlanTransition
from .test_plan_repository import (
    RunSpecNotFound,
    RunSpecTaskMaterializer,
    TestPlanConflict,
    TestPlanNotFound,
    TestPlanRepository,
    TestPlanVersionConflict,
)
from .test_plan_routes import (
    execution_batches_router,
    run_specs_router,
    test_plans_router,
)
from .test_plan_sql_repository import SqlTestPlanRepository
from .version_control_routes import version_control_router
from .version_control_service import (
    DetachedHead,
    DirtyWorkingTree,
    GitOperationFailed,
    GitOperationTimedOut,
    RemoteUnavailable,
    RepositoryUnavailable,
    SensitiveFilesStaged,
    VersionControlDisabled,
    VersionControlForbidden,
    VersionControlService,
)


def _problem(
    request: Request,
    status: int,
    code: str,
    detail: str,
    errors: list[ValidationErrorDetail] | None = None,
) -> JSONResponse:
    title = HTTPStatus(status).phrase
    body = Problem(
        title=title,
        status=status,
        detail=detail,
        instance=request.url.path,
        code=code,
        errors=errors,
    )
    return JSONResponse(
        status_code=status,
        content=body.model_dump(mode="json", exclude_none=True),
        media_type="application/problem+json",
    )


def create_app(
    repository: RunRepository | None = None,
    dispatcher: RunDispatcher | None = None,
    settings: Settings | None = None,
    artifact_store: ArtifactStore | None = None,
    quality_repository: QualityRepository | SqlQualityRepository | None = None,
    automation_repository: AutomationRepository | SqlAutomationRepository | None = None,
    case_generator: AiCaseGenerator | None = None,
    environment_repository: EnvironmentRepository | SqlEnvironmentRepository | None = None,
    test_plan_repository: TestPlanRepository | SqlTestPlanRepository | None = None,
    auth_repository: AuthRepositoryLike | None = None,
    project_repository: ProjectRepository | SqlProjectRepository | None = None,
    source_store: LocalSourceStore | None = None,
    executor_factory: ExecutorFactory | None = None,
    host_executor_factory: HostExecutorFactory | None = None,
    parameter_repository: ParameterRepository | SqlParameterRepository | None = None,
    version_control_service: VersionControlService | None = None,
    service_control_service: ServiceControlService | None = None,
) -> FastAPI:
    resolved_settings = settings or Settings.from_env()
    database_engine = None
    sessions = None
    if resolved_settings.database_url and (
        repository is None or quality_repository is None or automation_repository is None
        or environment_repository is None or test_plan_repository is None
        or auth_repository is None or project_repository is None
        or parameter_repository is None
    ):
        database_engine = create_database_engine(resolved_settings.database_url)
        sessions = create_session_factory(database_engine)
    if repository is None:
        repository = SqlRunRepository(sessions) if sessions else RunRepository()
    if quality_repository is None:
        quality_repository = (
            SqlQualityRepository(sessions) if sessions else QualityRepository()
        )
    if automation_repository is None:
        automation_repository = (
            SqlAutomationRepository(sessions)
            if sessions else AutomationRepository(quality_repository)
        )
    if parameter_repository is None:
        parameter_repository = (
            SqlParameterRepository(sessions)
            if sessions else ParameterRepository(automation_repository)
        )
    encryptor = SecretEncryptor(
        resolved_settings.secret_encryption_key,
        resolved_settings.secret_encryption_key_id,
    )
    if environment_repository is None:
        environment_repository = (
            SqlEnvironmentRepository(sessions, encryptor)
            if sessions else EnvironmentRepository(encryptor)
        )
    if test_plan_repository is None:
        test_plan_repository = (
            SqlTestPlanRepository(sessions)
            if sessions else TestPlanRepository(
                automation_repository, environment_repository, repository
            )
        )
    if auth_repository is None:
        auth_repository = (
            SqlAuthRepository(sessions) if sessions else AuthRepository()
        )
    if project_repository is None:
        project_repository = (
            SqlProjectRepository(sessions) if sessions else ProjectRepository()
        )
    if resolved_settings.auto_dispatch and dispatcher is None:
        if resolved_settings.local_runner_enabled:
            dispatcher = DatabaseRunDispatcher(repository)
        else:
            from workers.celery_app import create_celery_app

            dispatcher = CeleryRunDispatcher(create_celery_app(resolved_settings))
    artifact_store = artifact_store or LocalArtifactStore(
        resolved_settings.artifact_root
    )
    source_store = source_store or LocalSourceStore(
        resolved_settings.source_root,
        host_execution_enabled=resolved_settings.host_execution_enabled,
        host_project_roots=resolved_settings.host_project_roots,
    )
    local_runner_service = None
    if resolved_settings.local_runner_enabled:
        local_dispatcher = dispatcher or DatabaseRunDispatcher(repository)
        local_runner_service = LocalRunnerService(
            repository,
            test_plan_repository,
            local_dispatcher,
            source_store,
            artifact_store,
            resolved_settings.runner_work_root,
            image=resolved_settings.runner_image,
            network=resolved_settings.runner_network,
            max_workers=resolved_settings.runner_max_workers,
            poll_interval_seconds=resolved_settings.runner_poll_interval_seconds,
            executor_factory=executor_factory,
            host_executor_factory=host_executor_factory,
        )
    case_generator = case_generator or AiCaseGenerator()
    service_control_service = service_control_service or ServiceControlService(
        resolved_settings.service_control_enabled,
        resolved_settings.service_control_root,
        auto_restart_enabled=resolved_settings.version_control_auto_restart,
    )
    version_control_service = version_control_service or VersionControlService(
        resolved_settings.version_control_enabled,
        resolved_settings.version_control_root,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if local_runner_service is not None:
            local_runner_service.start()
        try:
            yield
        finally:
            if local_runner_service is not None:
                local_runner_service.stop()
            if database_engine is not None:
                database_engine.dispose()

    application = FastAPI(
        title="Smart Test Platform API", version="0.1.0", lifespan=lifespan
    )
    application.state.run_repository = repository
    application.state.run_dispatcher = dispatcher
    application.state.settings = resolved_settings
    application.state.database_engine = database_engine
    application.state.artifact_store = artifact_store
    application.state.source_store = source_store
    application.state.local_runner_service = local_runner_service
    application.state.quality_repository = quality_repository
    application.state.automation_repository = automation_repository
    application.state.parameter_repository = parameter_repository
    application.state.environment_repository = environment_repository
    application.state.configuration_resolver = ConfigurationResolver(
        environment_repository, encryptor
    )
    application.state.test_plan_repository = test_plan_repository
    application.state.run_spec_materializer = RunSpecTaskMaterializer(
        test_plan_repository
    )
    application.state.case_generator = case_generator
    application.state.auth_repository = auth_repository
    application.state.login_throttle = LoginThrottle()
    application.state.project_repository = project_repository
    application.state.version_control_service = version_control_service
    application.state.service_control_service = service_control_service

    @application.middleware("http")
    async def authentication_middleware(request: Request, call_next):
        path = request.url.path
        exempt = (
            request.method == "OPTIONS"
            or path.startswith("/health/")
            or path == "/api/v1/auth"
            or path.startswith("/api/v1/auth/")
            or path in {"/docs", "/docs/oauth2-redirect", "/openapi.json", "/redoc"}
        )
        if resolved_settings.auth_required and not exempt:
            token = request.cookies.get(SESSION_COOKIE_NAME)
            user = (
                auth_repository.resolve_session(session_token_digest(token))
                if token else None
            )
            if user is None:
                return _problem(
                    request, 401, "authentication_required", "Authentication required"
                )
            request.state.auth_user = user
        return await call_next(request)

    application.include_router(health_router)
    application.include_router(auth_router)
    application.include_router(project_router)
    application.include_router(runs_router)
    application.include_router(requirements_router)
    application.include_router(test_cases_router)
    application.include_router(automation_sources_router)
    application.include_router(automation_router)
    application.include_router(parameter_enum_router)
    application.include_router(script_parameter_router)
    application.include_router(case_scripts_router)
    application.include_router(ai_router)
    application.include_router(environment_router)
    application.include_router(test_plans_router)
    application.include_router(execution_batches_router)
    application.include_router(run_specs_router)
    application.include_router(version_control_router)
    application.include_router(service_control_router)

    @application.exception_handler(RequestValidationError)
    async def validation_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        errors = [
            ValidationErrorDetail(
                loc=list(error["loc"]),
                message=error["msg"],
                type=error["type"],
            )
            for error in exc.errors()
        ]
        return _problem(
            request, 422, "validation_error", "Request validation failed", errors
        )

    @application.exception_handler(InvalidCredentials)
    async def invalid_credentials_handler(
        request: Request, _: InvalidCredentials
    ) -> JSONResponse:
        return _problem(
            request, 401, "invalid_credentials", "Invalid username or password"
        )

    @application.exception_handler(AuthAlreadyInitialized)
    async def auth_initialized_handler(
        request: Request, _: AuthAlreadyInitialized
    ) -> JSONResponse:
        return _problem(
            request, 409, "auth_already_initialized",
            "Authentication is already initialized",
        )

    @application.exception_handler(UsernameConflict)
    async def username_conflict_handler(
        request: Request, _: UsernameConflict
    ) -> JSONResponse:
        return _problem(request, 409, "username_conflict", "Username is already in use")

    @application.exception_handler(ProjectNotFound)
    async def project_not_found_handler(
        request: Request, exc: ProjectNotFound
    ) -> JSONResponse:
        return _problem(request, 404, "project_not_found", str(exc))

    @application.exception_handler(ProjectNameConflict)
    async def project_name_conflict_handler(
        request: Request, exc: ProjectNameConflict
    ) -> JSONResponse:
        return _problem(request, 409, "project_name_conflict", str(exc))

    @application.exception_handler(ProjectVersionConflict)
    async def project_version_conflict_handler(
        request: Request, exc: ProjectVersionConflict
    ) -> JSONResponse:
        return _problem(request, 409, "project_version_conflict", str(exc))

    @application.exception_handler(ProjectInUse)
    async def project_in_use_handler(
        request: Request, exc: ProjectInUse
    ) -> JSONResponse:
        return _problem(request, 409, "project_in_use", str(exc))

    @application.exception_handler(InvalidProjectTransition)
    async def project_transition_handler(
        request: Request, exc: InvalidProjectTransition
    ) -> JSONResponse:
        return _problem(request, 409, "invalid_project_transition", str(exc))

    @application.exception_handler(LoginRateLimited)
    async def login_rate_limited_handler(
        request: Request, _: LoginRateLimited
    ) -> JSONResponse:
        return _problem(
            request, 429, "login_rate_limited", "Too many login attempts; try again later"
        )

    @application.exception_handler(RunNotFound)
    async def not_found_handler(request: Request, exc: RunNotFound) -> JSONResponse:
        return _problem(request, 404, "run_not_found", str(exc))

    @application.exception_handler(AttemptNotFound)
    async def attempt_not_found_handler(
        request: Request, exc: AttemptNotFound
    ) -> JSONResponse:
        return _problem(request, 404, "attempt_not_found", str(exc))

    @application.exception_handler(IdempotencyConflict)
    async def idempotency_handler(request: Request, exc: IdempotencyConflict) -> JSONResponse:
        return _problem(request, 409, "idempotency_conflict", str(exc))

    @application.exception_handler(TerminalRunConflict)
    async def terminal_handler(request: Request, exc: TerminalRunConflict) -> JSONResponse:
        return _problem(request, 409, "terminal_run_conflict", str(exc))

    @application.exception_handler(AttemptConflict)
    async def attempt_conflict_handler(
        request: Request, exc: AttemptConflict
    ) -> JSONResponse:
        return _problem(request, 409, "attempt_conflict", str(exc))

    @application.exception_handler(EventConflict)
    async def event_conflict_handler(
        request: Request, exc: EventConflict
    ) -> JSONResponse:
        return _problem(request, 409, "event_conflict", str(exc))

    @application.exception_handler(ArtifactNotFound)
    async def artifact_not_found_handler(
        request: Request, exc: ArtifactNotFound
    ) -> JSONResponse:
        return _problem(request, 404, "artifact_not_found", str(exc))

    @application.exception_handler(ArtifactConflict)
    async def artifact_conflict_handler(
        request: Request, exc: ArtifactConflict
    ) -> JSONResponse:
        return _problem(request, 409, "artifact_conflict", str(exc))

    @application.exception_handler(InvalidTransition)
    async def transition_handler(request: Request, exc: InvalidTransition) -> JSONResponse:
        return _problem(request, 409, "invalid_transition", str(exc))

    @application.exception_handler(RequirementNotFound)
    async def requirement_not_found_handler(
        request: Request, exc: RequirementNotFound
    ) -> JSONResponse:
        return _problem(request, 404, "requirement_not_found", str(exc))

    @application.exception_handler(TestCaseNotFound)
    async def test_case_not_found_handler(
        request: Request, exc: TestCaseNotFound
    ) -> JSONResponse:
        return _problem(request, 404, "test_case_not_found", str(exc))

    @application.exception_handler(AssetVersionConflict)
    async def asset_version_handler(
        request: Request, exc: AssetVersionConflict
    ) -> JSONResponse:
        return _problem(request, 409, "asset_version_conflict", str(exc))

    @application.exception_handler(TraceLinkConflict)
    async def trace_link_handler(
        request: Request, exc: TraceLinkConflict
    ) -> JSONResponse:
        return _problem(request, 409, "trace_link_conflict", str(exc))

    @application.exception_handler(InvalidAssetTransition)
    async def asset_transition_handler(
        request: Request, exc: InvalidAssetTransition
    ) -> JSONResponse:
        return _problem(request, 409, "invalid_asset_transition", str(exc))

    @application.exception_handler(ScriptNotFound)
    async def script_not_found_handler(
        request: Request, exc: ScriptNotFound
    ) -> JSONResponse:
        return _problem(request, 404, "automation_script_not_found", str(exc))

    @application.exception_handler(ScriptLinkConflict)
    async def script_link_handler(
        request: Request, exc: ScriptLinkConflict
    ) -> JSONResponse:
        return _problem(request, 409, "script_link_conflict", str(exc))

    @application.exception_handler(ParameterEnumNotFound)
    async def parameter_enum_not_found_handler(
        request: Request, exc: ParameterEnumNotFound
    ) -> JSONResponse:
        return _problem(request, 404, "parameter_enum_not_found", str(exc))

    @application.exception_handler(ParameterEnumVersionConflict)
    async def parameter_enum_version_handler(
        request: Request, exc: ParameterEnumVersionConflict
    ) -> JSONResponse:
        return _problem(request, 409, "parameter_enum_version_conflict", str(exc))

    @application.exception_handler(ParameterEnumConflict)
    async def parameter_enum_conflict_handler(
        request: Request, exc: ParameterEnumConflict
    ) -> JSONResponse:
        return _problem(request, 409, "parameter_enum_conflict", str(exc))

    @application.exception_handler(ScriptParameterConflict)
    async def script_parameter_conflict_handler(
        request: Request, exc: ScriptParameterConflict
    ) -> JSONResponse:
        return _problem(request, 409, "script_parameter_conflict", str(exc))

    @application.exception_handler(SourceStoreError)
    async def source_store_handler(
        request: Request, exc: SourceStoreError
    ) -> JSONResponse:
        return _problem(request, 422, "automation_source_invalid", str(exc))

    @application.exception_handler(EnvironmentNotFound)
    async def environment_not_found_handler(
        request: Request, exc: EnvironmentNotFound
    ) -> JSONResponse:
        return _problem(request, 404, "environment_not_found", str(exc))

    @application.exception_handler(EnvironmentConflict)
    async def environment_conflict_handler(
        request: Request, exc: EnvironmentConflict
    ) -> JSONResponse:
        return _problem(request, 409, "environment_conflict", str(exc))

    @application.exception_handler(EnvironmentVersionConflict)
    async def environment_version_handler(
        request: Request, exc: EnvironmentVersionConflict
    ) -> JSONResponse:
        return _problem(request, 409, "environment_version_conflict", str(exc))

    @application.exception_handler(InvalidEnvironmentTransition)
    async def environment_transition_handler(
        request: Request, exc: InvalidEnvironmentTransition
    ) -> JSONResponse:
        return _problem(request, 409, "environment_conflict", str(exc))

    @application.exception_handler(TestPlanNotFound)
    async def test_plan_not_found_handler(
        request: Request, exc: TestPlanNotFound
    ) -> JSONResponse:
        return _problem(request, 404, "test_plan_not_found", str(exc))

    @application.exception_handler(RunSpecNotFound)
    async def run_spec_not_found_handler(
        request: Request, exc: RunSpecNotFound
    ) -> JSONResponse:
        return _problem(request, 404, "run_spec_not_found", str(exc))

    @application.exception_handler(TestPlanVersionConflict)
    async def test_plan_version_handler(
        request: Request, exc: TestPlanVersionConflict
    ) -> JSONResponse:
        return _problem(request, 409, "test_plan_version_conflict", str(exc))

    @application.exception_handler(TestPlanConflict)
    @application.exception_handler(InvalidTestPlanTransition)
    async def test_plan_conflict_handler(
        request: Request, exc: TestPlanConflict | InvalidTestPlanTransition
    ) -> JSONResponse:
        return _problem(request, 409, "test_plan_conflict", str(exc))

    @application.exception_handler(SecretEncryptionUnavailable)
    @application.exception_handler(SecretDecryptionError)
    async def secret_unavailable_handler(
        request: Request, _: SecretEncryptionUnavailable | SecretDecryptionError
    ) -> JSONResponse:
        return _problem(
            request, 503, "secret_encryption_unavailable",
            "Secret encryption service is unavailable",
        )

    @application.exception_handler(CaseGenerationUnavailable)
    async def generation_unavailable_handler(
        request: Request, _: CaseGenerationUnavailable
    ) -> JSONResponse:
        return _problem(
            request, 503, "case_generation_unavailable",
            "AI case generation is unavailable",
        )

    @application.exception_handler(CaseGenerationError)
    async def generation_error_handler(
        request: Request, _: CaseGenerationError
    ) -> JSONResponse:
        return _problem(
            request, 502, "case_generation_failed",
            "AI provider returned an unusable response",
        )

    @application.exception_handler(DispatchUnavailable)
    async def dispatch_handler(
        request: Request, _: DispatchUnavailable
    ) -> JSONResponse:
        return _problem(
            request, 503, "dispatch_unavailable", "Run dispatch is unavailable"
        )

    @application.exception_handler(VersionControlForbidden)
    async def version_control_forbidden_handler(
        request: Request, _: VersionControlForbidden
    ) -> JSONResponse:
        return _problem(request, 403, "version_control_forbidden", "Administrator access required")

    @application.exception_handler(ServiceControlForbidden)
    async def service_control_forbidden_handler(
        request: Request, _: ServiceControlForbidden
    ) -> JSONResponse:
        return _problem(
            request, 403, "service_control_forbidden", "Administrator access required"
        )

    @application.exception_handler(ServiceControlUnavailable)
    async def service_control_unavailable_handler(
        request: Request, _: ServiceControlUnavailable
    ) -> JSONResponse:
        return _problem(
            request, 503, "service_control_unavailable",
            "Service supervisor is unavailable",
        )

    @application.exception_handler(VersionControlDisabled)
    async def version_control_disabled_handler(
        request: Request, _: VersionControlDisabled
    ) -> JSONResponse:
        return _problem(request, 503, "version_control_disabled", "Version control is disabled")

    @application.exception_handler(RepositoryUnavailable)
    async def repository_unavailable_handler(
        request: Request, _: RepositoryUnavailable
    ) -> JSONResponse:
        return _problem(request, 409, "repository_unavailable", "Configured repository is unavailable")

    @application.exception_handler(RemoteUnavailable)
    async def remote_unavailable_handler(
        request: Request, _: RemoteUnavailable
    ) -> JSONResponse:
        return _problem(request, 409, "remote_unavailable", "The origin remote is not configured")

    @application.exception_handler(DirtyWorkingTree)
    async def dirty_working_tree_handler(
        request: Request, _: DirtyWorkingTree
    ) -> JSONResponse:
        return _problem(request, 409, "dirty_working_tree", "Working tree must be clean before pull")

    @application.exception_handler(DetachedHead)
    async def detached_head_handler(
        request: Request, _: DetachedHead
    ) -> JSONResponse:
        return _problem(request, 409, "detached_head", "Repository has no current branch")

    @application.exception_handler(SensitiveFilesStaged)
    async def sensitive_files_handler(
        request: Request, _: SensitiveFilesStaged
    ) -> JSONResponse:
        return _problem(request, 422, "sensitive_files_staged", "Sensitive files cannot be published")

    @application.exception_handler(GitOperationTimedOut)
    async def git_timeout_handler(
        request: Request, _: GitOperationTimedOut
    ) -> JSONResponse:
        return _problem(request, 504, "git_operation_timed_out", "Version control operation timed out")

    @application.exception_handler(GitOperationFailed)
    async def git_failed_handler(
        request: Request, _: GitOperationFailed
    ) -> JSONResponse:
        return _problem(request, 502, "git_operation_failed", "Version control operation failed")

    @application.exception_handler(HTTPException)
    async def http_handler(request: Request, exc: HTTPException) -> JSONResponse:
        return _problem(request, exc.status_code, "http_error", str(exc.detail))

    @application.exception_handler(Exception)
    async def unexpected_handler(request: Request, _: Exception) -> JSONResponse:
        return _problem(request, 500, "internal_error", "Internal server error")

    return application


app = create_app()