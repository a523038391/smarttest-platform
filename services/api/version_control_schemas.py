from pydantic import Field, field_validator

from .schemas import ApiModel
from .version_control_service import VersionControlStatus


class VersionControlStatusResponse(ApiModel):
    enabled: bool
    repository_present: bool
    branch: str | None
    head_short: str | None
    clean: bool | None
    change_count: int
    changed_paths: list[str]
    remote_configured: bool
    restart_scheduled: bool = False

    @classmethod
    def from_record(
        cls, status: VersionControlStatus, *, restart_scheduled: bool = False,
    ) -> "VersionControlStatusResponse":
        return cls.model_validate(status, from_attributes=True).model_copy(
            update={"restart_scheduled": restart_scheduled}
        )


class PublishRequest(ApiModel):
    commit_message: str = Field(min_length=1, max_length=120)

    @field_validator("commit_message")
    @classmethod
    def validate_commit_message(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("commit message must not be blank")
        if any(ord(character) < 32 for character in normalized):
            raise ValueError("commit message must not contain control characters")
        return normalized