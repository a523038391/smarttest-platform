from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import UUID


class EnvironmentStatus(str, Enum):
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"


class ConfigurationCategory(str, Enum):
    ENVIRONMENT_VARIABLE = "environment_variable"
    COMMON_PARAMETER = "common_parameter"


ENVIRONMENT_TRANSITIONS = {
    EnvironmentStatus.DRAFT: {EnvironmentStatus.ACTIVE},
    EnvironmentStatus.ACTIVE: {EnvironmentStatus.ARCHIVED},
}


class InvalidEnvironmentTransition(ValueError):
    pass


def validate_environment_transition(
    current: EnvironmentStatus, target: EnvironmentStatus
) -> None:
    if current is EnvironmentStatus.ARCHIVED:
        raise InvalidEnvironmentTransition("an archived environment cannot be modified")
    if target != current and target not in ENVIRONMENT_TRANSITIONS[current]:
        raise InvalidEnvironmentTransition(
            f"cannot transition environment from {current.value} to {target.value}"
        )


@dataclass(frozen=True, slots=True)
class EncryptedSecret:
    ciphertext: bytes = field(repr=False)
    tag: bytes = field(repr=False)
    nonce: bytes = field(repr=False)
    wrapped_dek: bytes = field(repr=False)
    wrap_nonce: bytes = field(repr=False)
    key_id: str = field(repr=False)
    algorithm: str = "AES-256-GCM"


@dataclass(frozen=True, slots=True)
class ConfigurationValueInput:
    name: str
    value: Any = field(default=None, repr=False)
    secret: bool = False
    secret_ref: UUID | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class ConfigurationValue:
    category: ConfigurationCategory
    name: str
    secret: bool
    value: Any = field(default=None, repr=False)
    secret_ref: UUID | None = field(default=None, repr=False)
    encrypted: EncryptedSecret | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class EnvironmentRecord:
    id: UUID
    project_id: UUID
    name: str
    status: EnvironmentStatus
    revision: int
    state_version: int
    values: tuple[ConfigurationValue, ...] = field(repr=False)
    created_at: datetime
    updated_at: datetime

    def update(
        self,
        *,
        name: str,
        status: EnvironmentStatus,
        values: tuple[ConfigurationValue, ...],
    ) -> "EnvironmentRecord":
        validate_environment_transition(self.status, status)
        now = datetime.now(timezone.utc)
        return replace(
            self,
            name=name,
            status=status,
            revision=self.revision + 1,
            state_version=self.state_version + 1,
            values=values,
            updated_at=now,
        )


@dataclass(frozen=True, slots=True)
class ConfigurationSnapshot:
    environment_id: UUID
    revision: int
    environment_variables: dict[str, str] = field(repr=False)
    common_parameters: dict[str, Any] = field(repr=False)