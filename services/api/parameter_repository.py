import json
import re
from copy import deepcopy
from datetime import datetime, timezone
from threading import RLock
from typing import Protocol, Sequence
from uuid import UUID, uuid4

from .automation_domain import AutomationScriptRecord
from .parameter_domain import (
    JsonScalar, ParameterEnumOption, ParameterEnumSetRecord,
    ParameterInputType, ScriptParameterDefinition,
)


PARAMETER_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")


class ParameterEnumNotFound(LookupError):
    pass


class ParameterEnumConflict(ValueError):
    pass


class ParameterEnumVersionConflict(ParameterEnumConflict):
    pass


class ScriptParameterConflict(ValueError):
    pass


class ScriptReader(Protocol):
    def get_script(self, script_id: UUID) -> AutomationScriptRecord: ...


def json_identity(value: object) -> str:
    if value is not None and not isinstance(value, (str, int, float, bool)):
        raise ValueError("values must be JSON scalars")
    try:
        return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    except (TypeError, ValueError):
        raise ValueError("values must be finite JSON scalars") from None


def validate_enum_values(
    name: str, options: Sequence[ParameterEnumOption]
) -> tuple[str, tuple[ParameterEnumOption, ...]]:
    normalized = name.strip()
    if not normalized or len(normalized) > 255:
        raise ValueError("enum name must contain 1 to 255 characters")
    identities: set[str] = set()
    validated: list[ParameterEnumOption] = []
    for option in options:
        label = option.label.strip()
        if not label or len(label) > 255:
            raise ValueError("option labels must contain 1 to 255 characters")
        identity = json_identity(option.value)
        if identity in identities:
            raise ValueError("enum option values must be unique")
        identities.add(identity)
        validated.append(ParameterEnumOption(label, deepcopy(option.value)))
    return normalized, tuple(validated)


def validate_parameter_definitions(
    definitions: Sequence[ScriptParameterDefinition],
) -> tuple[ScriptParameterDefinition, ...]:
    names: set[str] = set()
    positions: set[int] = set()
    validated: list[ScriptParameterDefinition] = []
    for item in definitions:
        if not PARAMETER_NAME_PATTERN.fullmatch(item.name) or item.name in names:
            raise ScriptParameterConflict("parameter names must be unique valid identifiers")
        label = item.label.strip()
        if not label or len(label) > 255:
            raise ScriptParameterConflict("parameter labels must not be blank")
        if item.position < 0 or item.position in positions:
            raise ScriptParameterConflict("parameter positions must be unique non-negative integers")
        json_identity(item.default_value)
        if item.input_type is ParameterInputType.ENUM and item.enum_set_id is None:
            raise ScriptParameterConflict("enum parameters require enum_set_id")
        if item.input_type is not ParameterInputType.ENUM and item.enum_set_id is not None:
            raise ScriptParameterConflict("enum_set_id is only valid for enum parameters")
        names.add(item.name)
        positions.add(item.position)
        validated.append(ScriptParameterDefinition(
            item.name, label, item.input_type, item.required, item.enum_set_id,
            deepcopy(item.default_value), item.position,
        ))
    return tuple(sorted(validated, key=lambda item: item.position))


class ParameterRepository:
    def __init__(self, scripts: ScriptReader) -> None:
        self._scripts = scripts
        self._lock = RLock()
        self._enums: dict[UUID, ParameterEnumSetRecord] = {}
        self._names: dict[tuple[UUID, str], UUID] = {}
        self._parameters: dict[UUID, tuple[ScriptParameterDefinition, ...]] = {}

    def create_enum_set(self, project_id: UUID, name: str, description: str,
                        options: Sequence[ParameterEnumOption]) -> ParameterEnumSetRecord:
        normalized, validated = validate_enum_values(name, options)
        with self._lock:
            if (project_id, normalized) in self._names:
                raise ParameterEnumConflict("an enum set with this project and name exists")
            now = datetime.now(timezone.utc)
            record = ParameterEnumSetRecord(
                uuid4(), project_id, normalized, description, validated, 0, now, now
            )
            self._enums[record.id] = record
            self._names[(project_id, normalized)] = record.id
            return deepcopy(record)

    def get_enum_set(self, enum_set_id: UUID) -> ParameterEnumSetRecord:
        with self._lock:
            try:
                return deepcopy(self._enums[enum_set_id])
            except KeyError:
                raise ParameterEnumNotFound(f"parameter enum {enum_set_id} was not found") from None

    def list_enum_sets(self, project_id: UUID) -> list[ParameterEnumSetRecord]:
        with self._lock:
            records = [item for item in self._enums.values() if item.project_id == project_id]
            return deepcopy(sorted(records, key=lambda item: item.created_at))

    def update_enum_set(self, enum_set_id: UUID, *, expected_version: int, name: str,
                        description: str, options: Sequence[ParameterEnumOption]) -> ParameterEnumSetRecord:
        normalized, validated = validate_enum_values(name, options)
        with self._lock:
            current = self.get_enum_set(enum_set_id)
            if current.state_version != expected_version:
                raise ParameterEnumVersionConflict(
                    f"state version mismatch: expected {expected_version}, current {current.state_version}"
                )
            owner = self._names.get((current.project_id, normalized))
            if owner is not None and owner != enum_set_id:
                raise ParameterEnumConflict("an enum set with this project and name exists")
            self._validate_existing_defaults(enum_set_id, validated)
            updated = current.update(name=normalized, description=description, options=validated)
            if current.name != normalized:
                del self._names[(current.project_id, current.name)]
                self._names[(current.project_id, normalized)] = enum_set_id
            self._enums[enum_set_id] = updated
            return deepcopy(updated)

    def delete_enum_set(self, enum_set_id: UUID) -> None:
        with self._lock:
            current = self.get_enum_set(enum_set_id)
            if any(item.enum_set_id == enum_set_id for values in self._parameters.values() for item in values):
                raise ParameterEnumConflict("parameter enum is referenced by a script parameter")
            del self._enums[enum_set_id]
            del self._names[(current.project_id, current.name)]

    def list_script_parameters(self, script_id: UUID) -> list[ScriptParameterDefinition]:
        with self._lock:
            self._scripts.get_script(script_id)
            return deepcopy(list(self._parameters.get(script_id, ())))

    def replace_script_parameters(
        self, script_id: UUID, definitions: Sequence[ScriptParameterDefinition]
    ) -> list[ScriptParameterDefinition]:
        validated = validate_parameter_definitions(definitions)
        with self._lock:
            script = self._scripts.get_script(script_id)
            self._validate_parameter_enums(script.project_id, validated)
            self._parameters[script_id] = validated
            return deepcopy(list(validated))

    def _validate_parameter_enums(
        self, project_id: UUID, definitions: Sequence[ScriptParameterDefinition]
    ) -> None:
        for item in definitions:
            if item.enum_set_id is None:
                continue
            try:
                enum_set = self.get_enum_set(item.enum_set_id)
            except ParameterEnumNotFound:
                raise ScriptParameterConflict("parameter enum was not found") from None
            if enum_set.project_id != project_id:
                raise ScriptParameterConflict("parameter enum belongs to a different project")
            if json_identity(item.default_value) not in {
                json_identity(option.value) for option in enum_set.options
            }:
                raise ScriptParameterConflict("enum default_value must match an enum option")

    def _validate_existing_defaults(
        self, enum_set_id: UUID, options: Sequence[ParameterEnumOption]
    ) -> None:
        identities = {json_identity(option.value) for option in options}
        for values in self._parameters.values():
            for item in values:
                if item.enum_set_id == enum_set_id and json_identity(item.default_value) not in identities:
                    raise ParameterEnumConflict("enum options cannot invalidate a referenced default")