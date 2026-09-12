from fastapi import APIRouter, Depends, Request

from .auth_domain import UserRole
from .config import Settings
from .version_control_schemas import PublishRequest, VersionControlStatusResponse
from .version_control_service import VersionControlForbidden, VersionControlService


version_control_router = APIRouter(
    prefix="/api/v1/version-control", tags=["version-control"]
)


def get_version_control_service(request: Request) -> VersionControlService:
    return request.app.state.version_control_service


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


@version_control_router.post(
    "/pull", response_model=VersionControlStatusResponse,
    dependencies=[Depends(require_admin)],
)
def pull(
    service: VersionControlService = Depends(get_version_control_service),
) -> VersionControlStatusResponse:
    return VersionControlStatusResponse.from_record(service.pull())


@version_control_router.post(
    "/publish", response_model=VersionControlStatusResponse,
    dependencies=[Depends(require_admin)],
)
def publish(
    body: PublishRequest,
    service: VersionControlService = Depends(get_version_control_service),
) -> VersionControlStatusResponse:
    return VersionControlStatusResponse.from_record(service.publish(body.commit_message))