from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from .environment_domain import (
    ConfigurationCategory,
    ConfigurationValue,
    ConfigurationValueInput,
    EnvironmentRecord,
    EnvironmentStatus,
)
from .environment_repository import validate_inputs
from .schemas import ApiModel


class ConfigurationValueWrite(ApiModel):
    name: str = Field(min_length=1, max_length=128)
    value: Any = Field(default=None, repr=False)
    secret: bool = False
    secret_ref: UUID | None = None

    def to_input(self) -> ConfigurationValueInput:
        return ConfigurationValueInput(
            self.name, self.value, self.secret, self.secret_ref
        )


class _EnvironmentValues(ApiModel):
    name: str = Field(min_length=1, max_length=255)
    environment_variables: list[ConfigurationValueWrite] = Field(
        default_factory=list, max_length=100
    )
    common_parameters: list[ConfigurationValueWrite] = Field(
        default_factory=list, max_length=100
    )

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("environment name must not be blank")
        return normalized

    @model_validator(mode="after")
    def validate_configuration_values(self) -> "_EnvironmentValues":
        validate_inputs(
            ConfigurationCategory.ENVIRONMENT_VARIABLE,
            [item.to_input() for item in self.environment_variables],
        )
        validate_inputs(
            ConfigurationCategory.COMMON_PARAMETER,
            [item.to_input() for item in self.common_parameters],
        )
        return self


class EnvironmentCreate(_EnvironmentValues):
    project_id: UUID


class EnvironmentUpdate(_EnvironmentValues):
    status: EnvironmentStatus
    state_version: int = Field(ge=0)


class PublicConfigurationValueResponse(ApiModel):
    name: str
    secret: Literal[False] = False
    value: Any


class SecretConfigurationValueResponse(ApiModel):
    name: str
    secret: Literal[True] = True
    configured: Literal[True] = True
    secret_ref: UUID


ConfigurationValueResponse = (
    PublicConfigurationValueResponse | SecretConfigurationValueResponse
)


def configuration_response(item: ConfigurationValue) -> ConfigurationValueResponse:
    if item.secret:
        if item.secret_ref is None:
            raise ValueError("configured secret is missing its reference")
        return SecretConfigurationValueResponse(
            name=item.name, secret_ref=item.secret_ref
        )
    return PublicConfigurationValueResponse(name=item.name, value=item.value)


class EnvironmentResponse(ApiModel):
    id: UUID
    project_id: UUID
    name: str
    status: EnvironmentStatus
    revision: int
    state_version: int
    environment_variables: list[ConfigurationValueResponse]
    common_parameters: list[ConfigurationValueResponse]
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_record(cls, record: EnvironmentRecord) -> "EnvironmentResponse":
        by_category = {
            category: [
                configuration_response(item)
                for item in record.values if item.category is category
            ]
            for category in ConfigurationCategory
        }
        return cls(
            id=record.id, project_id=record.project_id, name=record.name,
            status=record.status, revision=record.revision,
            state_version=record.state_version,
            environment_variables=by_category[
                ConfigurationCategory.ENVIRONMENT_VARIABLE
            ],
            common_parameters=by_category[ConfigurationCategory.COMMON_PARAMETER],
            created_at=record.created_at, updated_at=record.updated_at,
        )


class EnvironmentListResponse(ApiModel):
    items: list[EnvironmentResponse]
    total: int