from fastapi import APIRouter, Depends, Request, status

from .auth_domain import UserRole
from .config import Settings
from .service_control_schemas import (
    RestartServiceRequest,
    RestartServiceResponse,
    ServiceControlStatusResponse,
)
from .service_control_service import (
    RESTART_DELAY_SECONDS,
    ServiceControlForbidden,
    ServiceControlService,
)


service_control_router = APIRouter(
    prefix="/api/v1/service-control", tags=["service-control"]
)


def get_service_control_service(request: Request) -> ServiceControlService:
    return request.app.state.service_control_service


def require_service_control_admin(request: Request) -> None:
    settings: Settings = request.app.state.settings
    if not settings.auth_required:
        return
    user = getattr(request.state, "auth_user", None)
    if user is None or not user.is_active or user.role != UserRole.ADMIN:
        raise ServiceControlForbidden("administrator access is required")


@service_control_router.get(
    "/status",
    response_model=ServiceControlStatusResponse,
    dependencies=[Depends(require_service_control_admin)],
)
def service_control_status(
    service: ServiceControlService = Depends(get_service_control_service),
) -> ServiceControlStatusResponse:
    return ServiceControlStatusResponse.from_record(service.status())


@service_control_router.post(
    "/restart",
    response_model=RestartServiceResponse,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_service_control_admin)],
)
def restart_service(
    body: RestartServiceRequest,
    service: ServiceControlService = Depends(get_service_control_service),
) -> RestartServiceResponse:
    request = service.request_restart(body.target)
    return RestartServiceResponse(
        accepted=True,
        target=request.target,
        reconnect_after_seconds=RESTART_DELAY_SECONDS,
    )