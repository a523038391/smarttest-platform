from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from .environment_repository import ConfigurationResolver
from .environment_repository import EnvironmentRepository
from .environment_schemas import (
    EnvironmentAuthTestResponse,
    EnvironmentCreate,
    EnvironmentListResponse,
    EnvironmentResponse,
    EnvironmentUpdate,
)
from .environment_sql_repository import SqlEnvironmentRepository
from .load_test_auth import LoadTestAuthMode, LoadTestAuthResolver
from .load_test_executor import LoadTestExecutionError, LoadTestExecutor


environment_router = APIRouter(
    prefix="/api/v1/environments", tags=["environments"]
)


def get_environment_repository(
    request: Request,
) -> EnvironmentRepository | SqlEnvironmentRepository:
    return request.app.state.environment_repository


def get_configuration_resolver(request: Request) -> ConfigurationResolver:
    return request.app.state.configuration_resolver


def get_load_test_executor(request: Request) -> LoadTestExecutor:
    return request.app.state.load_test_executor


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


@environment_router.post(
    "/{environment_id}/test-auth", response_model=EnvironmentAuthTestResponse,
)
def test_environment_authentication(
    environment_id: UUID,
    repository: EnvironmentRepository | SqlEnvironmentRepository = Depends(
        get_environment_repository
    ),
    resolver: ConfigurationResolver = Depends(get_configuration_resolver),
    executor: LoadTestExecutor = Depends(get_load_test_executor),
) -> EnvironmentAuthTestResponse:
    environment = repository.get_environment(environment_id)
    config = LoadTestAuthResolver(repository, resolver).resolve(
        environment.project_id, environment_id
    )
    if config is None or config.mode is None:
        return EnvironmentAuthTestResponse(
            success=False, mode="DIRECT_TOKEN", message="环境未配置鉴权方式。"
        )
    mode = "LOGIN" if config.mode is LoadTestAuthMode.LOGIN else "DIRECT_TOKEN"
    try:
        executor.test_authentication(config)
    except LoadTestExecutionError as exc:
        if config.mode is not LoadTestAuthMode.LOGIN:
            message = "Token 或基础域名配置校验失败。"
        elif exc.code == "login_http_error":
            message = f"登录接口返回 HTTP {exc.status}，请检查地址和账号密码。"
        else:
            message = {
                "login_configuration_invalid": "登录配置不完整，请重新填写账号和密码。",
                "login_response_invalid": "登录接口响应不是有效 JSON。",
                "login_token_path_missing": "登录成功，但响应中不存在配置的 Token 路径。",
                "login_token_invalid": "Token 路径存在，但值不是非空字符串。",
                "login_request_failed": "登录接口请求失败，请检查域名、端口和网络。",
            }.get(exc.code, "登录或 Token 提取失败，请检查地址、账号密码和 Token 路径。")
        return EnvironmentAuthTestResponse(success=False, mode=mode, message=message)
    message = (
        "登录成功，已从响应中提取 Token。"
        if config.mode is LoadTestAuthMode.LOGIN
        else "基础域名与 Token 配置校验通过。"
    )
    return EnvironmentAuthTestResponse(success=True, mode=mode, message=message)


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