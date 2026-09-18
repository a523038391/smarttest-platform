"""Resolve project environment conventions into safe load-test runtime auth."""
from dataclasses import dataclass, field
from enum import Enum
from uuid import UUID

from packages.protocol import SecretReference

from .environment_domain import ConfigurationCategory, EnvironmentStatus
from .environment_repository import (
    ConfigurationResolver, EnvironmentConflict, EnvironmentNotFound, EnvironmentRepository,
)
from .environment_sql_repository import SqlEnvironmentRepository


class LoadTestAuthMode(str, Enum):
    TOKEN = "TOKEN"
    LOGIN = "LOGIN"


@dataclass(frozen=True, slots=True)
class LoadTestAuthConfiguration:
    base_url: str | None = None
    mode: LoadTestAuthMode | None = None
    login_url: str | None = None
    account_field: str = "username"
    password_field: str = "password"
    token_path: str = "data.token"
    auth_header: str = "Authorization"
    auth_prefix: str = "Bearer"
    token: str | None = field(default=None, repr=False)
    account: str | None = field(default=None, repr=False)
    password: str | None = field(default=None, repr=False)


EnvironmentRepositoryLike = EnvironmentRepository | SqlEnvironmentRepository
_PUBLIC_KEYS = {
    "BASE_URL", "AUTH_MODE", "LOGIN_URL", "ACCOUNT_FIELD", "PASSWORD_FIELD",
    "TOKEN_PATH", "AUTH_HEADER", "AUTH_PREFIX",
}
_SECRET_KEYS = {"AUTH_TOKEN", "AUTH_ACCOUNT", "AUTH_PASSWORD"}


class LoadTestAuthResolver:
    def __init__(self, repository: EnvironmentRepositoryLike,
                 configuration_resolver: ConfigurationResolver) -> None:
        self._repository = repository
        self._resolver = configuration_resolver

    def resolve(self, project_id: UUID,
                environment_id: UUID | None) -> LoadTestAuthConfiguration | None:
        if environment_id is None:
            return None
        environment = self._repository.get_environment(environment_id)
        if environment.project_id != project_id:
            raise EnvironmentNotFound(f"environment {environment_id} was not found")
        if environment.status is not EnvironmentStatus.ACTIVE:
            raise EnvironmentConflict("only an active environment can be used by a load test")

        values = {
            item.name: item for item in environment.values
            if item.category is ConfigurationCategory.ENVIRONMENT_VARIABLE
        }
        if any(values[key].secret for key in _PUBLIC_KEYS if key in values):
            raise EnvironmentConflict("load test public configuration keys cannot be secret")
        if any(not values[key].secret for key in _SECRET_KEYS if key in values):
            raise EnvironmentConflict("load test credential keys must be secret")
        public = self._resolver.snapshot(
            project_id, environment.id, environment.revision
        ).environment_variables
        mode_value = self._optional_string(public, "AUTH_MODE")
        try:
            normalized_mode = mode_value.upper() if mode_value else None
            if normalized_mode == "DIRECT_TOKEN":
                normalized_mode = "TOKEN"
            mode = LoadTestAuthMode(normalized_mode) if normalized_mode else None
        except ValueError:
            raise EnvironmentConflict("AUTH_MODE must be TOKEN or LOGIN") from None

        required = (
            ("AUTH_TOKEN",) if mode is LoadTestAuthMode.TOKEN else
            ("AUTH_ACCOUNT", "AUTH_PASSWORD") if mode is LoadTestAuthMode.LOGIN else ()
        )
        references = []
        for key in required:
            value = values.get(key)
            if value is None or not value.secret or value.secret_ref is None:
                raise EnvironmentConflict(f"{key} must be a configured secret")
            references.append(SecretReference(
                category=value.category.value, name=key, secret_ref=value.secret_ref,
            ))
        resolved = self._resolver.snapshot(
            project_id, environment.id, environment.revision, references
        ).environment_variables
        login_url = self._optional_string(public, "LOGIN_URL")
        if mode is LoadTestAuthMode.LOGIN and not login_url:
            raise EnvironmentConflict("LOGIN_URL is required for LOGIN authentication")
        return LoadTestAuthConfiguration(
            base_url=self._optional_string(public, "BASE_URL"), mode=mode,
            login_url=login_url,
            account_field=self._string(public, "ACCOUNT_FIELD", "username"),
            password_field=self._string(public, "PASSWORD_FIELD", "password"),
            token_path=self._string(public, "TOKEN_PATH", "data.token"),
            auth_header=self._string(public, "AUTH_HEADER", "Authorization"),
            auth_prefix=self._string(public, "AUTH_PREFIX", "Bearer", allow_empty=True),
            token=resolved.get("AUTH_TOKEN"), account=resolved.get("AUTH_ACCOUNT"),
            password=resolved.get("AUTH_PASSWORD"),
        )

    @staticmethod
    def _optional_string(values: dict[str, str], key: str) -> str | None:
        value = values.get(key)
        if value is None:
            return None
        if not isinstance(value, str) or not value.strip():
            raise EnvironmentConflict(f"{key} must be a non-blank string")
        return value.strip()

    @classmethod
    def _string(cls, values: dict[str, str], key: str, default: str,
                allow_empty: bool = False) -> str:
        value = values.get(key, default)
        if not isinstance(value, str) or (not allow_empty and not value.strip()):
            raise EnvironmentConflict(f"{key} must be a string")
        return value if allow_empty else value.strip()