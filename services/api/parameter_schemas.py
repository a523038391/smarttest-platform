from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import Field, RootModel, field_validator, model_validator

from .parameter_domain import (
    ParameterEnumOption, ParameterEnumSetRecord, ParameterInputType,
    ScriptParameterDefinition,
)
from .parameter_repository import (
    json_identity, validate_enum_values, validate_parameter_definitions,
)
from .schemas import ApiModel


class ParameterEnumOptionWrite(ApiModel):
    label: str = Field(min_length=1, max_length=255)
    value: Any

    @field_validator("value")
    @classmethod
    def validate_value(cls, value: object) -> object:
        json_identity(value)
        return value

    def to_domain(self) -> ParameterEnumOption:
        return ParameterEnumOption(self.label, self.value)


class _ParameterEnumBody(ApiModel):
    name: str = Field(min_length=1, max_length=255)
    description: str = Field(default="", max_length=100_000)
    options: list[ParameterEnumOptionWrite] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_enum(self) -> "_ParameterEnumBody":
        normalized, _ = validate_enum_values(
            self.name, [item.to_domain() for item in self.options]
        )
        self.name = normalized
        return self


class ParameterEnumCreate(_ParameterEnumBody):
    project_id: UUID


class ParameterEnumUpdate(_ParameterEnumBody):
    state_version: int = Field(ge=0)


class ParameterEnumOptionResponse(ApiModel):
    label: str
    value: Any


class ParameterEnumResponse(ApiModel):
    id: UUID
    project_id: UUID
    name: str
    description: str
    options: list[ParameterEnumOptionResponse]
    state_version: int
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_record(cls, record: ParameterEnumSetRecord) -> "ParameterEnumResponse":
        return cls.model_validate(record, from_attributes=True)


class ParameterEnumListResponse(ApiModel):
    items: list[ParameterEnumResponse]
    total: int


class ScriptParameterWrite(ApiModel):
    name: str = Field(min_length=1, max_length=128)
    label: str = Field(min_length=1, max_length=255)
    input_type: ParameterInputType
    required: bool
    enum_set_id: UUID | None
    default_value: Any
    position: int = Field(ge=0)

    @field_validator("default_value")
    @classmethod
    def validate_default(cls, value: object) -> object:
        json_identity(value)
        return value

    def to_domain(self) -> ScriptParameterDefinition:
        return ScriptParameterDefinition(
            self.name, self.label, self.input_type, self.required,
            self.enum_set_id, self.default_value, self.position,
        )


class ScriptParameterReplacement(RootModel[list[ScriptParameterWrite]]):
    @model_validator(mode="after")
    def validate_definitions(self) -> "ScriptParameterReplacement":
        validate_parameter_definitions([item.to_domain() for item in self.root])
        return self


class ScriptParameterResponse(ApiModel):
    name: str
    label: str
    input_type: ParameterInputType
    required: bool
    enum_set_id: UUID | None
    default_value: Any
    position: int

    @classmethod
    def from_record(cls, record: ScriptParameterDefinition) -> "ScriptParameterResponse":
        return cls.model_validate(record, from_attributes=True)