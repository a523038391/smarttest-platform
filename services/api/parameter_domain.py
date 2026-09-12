from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import Enum
from typing import TypeAlias
from uuid import UUID


JsonScalar: TypeAlias = str | int | float | bool | None


class ParameterInputType(str, Enum):
    TEXT = "text"
    NUMBER = "number"
    INTEGER = "integer"
    BOOLEAN = "boolean"
    ENUM = "enum"


@dataclass(frozen=True, slots=True)
class ParameterEnumOption:
    label: str
    value: JsonScalar


@dataclass(frozen=True, slots=True)
class ParameterEnumSetRecord:
    id: UUID
    project_id: UUID
    name: str
    description: str
    options: tuple[ParameterEnumOption, ...]
    state_version: int
    created_at: datetime
    updated_at: datetime

    def update(
        self,
        *,
        name: str,
        description: str,
        options: tuple[ParameterEnumOption, ...],
    ) -> "ParameterEnumSetRecord":
        return replace(
            self,
            name=name,
            description=description,
            options=options,
            state_version=self.state_version + 1,
            updated_at=datetime.now(timezone.utc),
        )


@dataclass(frozen=True, slots=True)
class ScriptParameterDefinition:
    name: str
    label: str
    input_type: ParameterInputType
    required: bool
    enum_set_id: UUID | None
    default_value: JsonScalar
    position: int