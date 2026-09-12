from datetime import datetime
from uuid import UUID

from pydantic import Field, field_validator

from .auth_domain import UserRecord, UserRole, validate_username
from .schemas import ApiModel


class _Credentials(ApiModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=6, max_length=128, repr=False)

    @field_validator("username")
    @classmethod
    def validate_username(cls, value: str) -> str:
        return validate_username(value)


class SetupRequest(_Credentials):
    display_name: str = Field(min_length=1, max_length=128)

    @field_validator("display_name")
    @classmethod
    def normalize_display_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("display name must not be blank")
        return normalized


class LoginRequest(_Credentials):
    pass


class AuthStatusResponse(ApiModel):
    setup_required: bool
    auth_required: bool
    authenticated: bool
    user: "UserResponse | None" = None


class UserResponse(ApiModel):
    id: UUID
    username: str
    display_name: str
    role: UserRole
    is_active: bool
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_record(cls, user: UserRecord) -> "UserResponse":
        return cls(
            id=user.id, username=user.username, display_name=user.display_name,
            role=user.role, is_active=user.is_active, created_at=user.created_at,
            updated_at=user.updated_at,
        )