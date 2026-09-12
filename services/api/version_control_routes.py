from fastapi import APIRouter, Depends, Request

from .auth_domain import UserRole
from .config import Settings
from .service_control_service import (
    ServiceControlService,
    ServiceControlUnavailable,
    ServiceTarget,
)
from .version_control_schemas import PublishRequest, VersionControlStatusResponse
from .version_control_service import VersionControlForbidden, VersionControlService


version_control_router = APIRouter(
    prefix="/api/v1/version-control", tags=["version-control"]
)


def get_version_control_service(request: Request) -> VersionControlService:
    return request.app.state.version_control_service


def get_service_control_service(request: Request) -> ServiceControlService:
    return request.app.state.service_control_service


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def require_admin(request: Request) -> None:
    settings: Settings = request.app.state.settings
    if not settings.auth_required:
        return
    user = getattr(request.state, "auth_user", None)
    if user is None or not user.is_active or user.role != UserRole.ADMIN:
        raise VersionControlForbidden("administrator access is required")


@version_control_router.get(
    "/status", response_model=VersionControlStatusResponse,
    dependencies=[Depends(require_admin)],
)
def version_control_status(
    service: VersionControlService = Depends(get_version_control_service),
) -> VersionControlStatusResponse:
    return VersionControlStatusResponse.from_record(service.status())


def _schedule_restart(
    settings: Settings, service: ServiceControlService,
) -> bool:
    if not settings.version_control_auto_restart:
        return False
    try:
        service.request_restart(ServiceTarget.ALL)
    except ServiceControlUnavailable:
        return False
    return True


@version_control_router.post(
    "/pull", response_model=VersionControlStatusResponse,
    dependencies=[Depends(require_admin)],
)
def pull(
    service: VersionControlService = Depends(get_version_control_service),
    service_control: ServiceControlService = Depends(get_service_control_service),
    settings: Settings = Depends(get_settings),
) -> VersionControlStatusResponse:
    result = service.pull()
    return VersionControlStatusResponse.from_record(
        result, restart_scheduled=_schedule_restart(settings, service_control)
    )


@version_control_router.post(
    "/publish", response_model=VersionControlStatusResponse,
    dependencies=[Depends(require_admin)],
)
def publish(
    body: PublishRequest,
    service: VersionControlService = Depends(get_version_control_service),
    service_control: ServiceControlService = Depends(get_service_control_service),
    settings: Settings = Depends(get_settings),
) -> VersionControlStatusResponse:
    result = service.publish(body.commit_message)
    return VersionControlStatusResponse.from_record(
        result, restart_scheduled=_schedule_restart(settings, service_control)
    )