from .schemas import ApiModel
from .service_control_service import ServiceControlStatus, ServiceTarget


class ServiceControlStatusResponse(ApiModel):
    enabled: bool
    supervisor_online: bool
    auto_restart_enabled: bool

    @classmethod
    def from_record(cls, status: ServiceControlStatus) -> "ServiceControlStatusResponse":
        return cls.model_validate(status, from_attributes=True)


class RestartServiceRequest(ApiModel):
    target: ServiceTarget


class RestartServiceResponse(ApiModel):
    accepted: bool
    target: ServiceTarget
    reconnect_after_seconds: int