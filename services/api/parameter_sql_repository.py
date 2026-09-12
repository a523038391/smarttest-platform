from datetime import datetime, timezone
from typing import Sequence
from uuid import UUID, uuid4

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from .automation_models import AutomationScriptModel
from .automation_repository import ScriptNotFound
from .parameter_domain import (
    ParameterEnumOption, ParameterEnumSetRecord, ParameterInputType,
    ScriptParameterDefinition,
)
from .parameter_models import AutomationScriptParameterModel, ParameterEnumSetModel
from .parameter_repository import (
    ParameterEnumConflict, ParameterEnumNotFound, ParameterEnumVersionConflict,
    ScriptParameterConflict, json_identity, validate_enum_values,
    validate_parameter_definitions,
)


class SqlParameterRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def create_enum_set(
        self, project_id: UUID, name: str, description: str,
        options: Sequence[ParameterEnumOption],
    ) -> ParameterEnumSetRecord:
        normalized, validated = validate_enum_values(name, options)
        now = datetime.now(timezone.utc)
        record = ParameterEnumSetRecord(
            uuid4(), project_id, normalized, description, validated, 0, now, now
        )
        try:
            with self._session_factory() as session, session.begin():
                session.add(ParameterEnumSetModel(
                    id=str(record.id), project_id=str(project_id), name=normalized,
                    description=description, options=self._option_values(validated),
                    state_version=0, created_at=now, updated_at=now,
                ))
        except IntegrityError:
            raise ParameterEnumConflict(
                "an enum set with this project and name exists"
            ) from None
        return record

    def get_enum_set(self, enum_set_id: UUID) -> ParameterEnumSetRecord:
        with self._session_factory() as session:
            model = session.get(ParameterEnumSetModel, str(enum_set_id))
            if model is None:
                raise self._not_found(enum_set_id)
            return self._enum_record(model)

    def list_enum_sets(self, project_id: UUID) -> list[ParameterEnumSetRecord]:
        with self._session_factory() as session:
            query = select(ParameterEnumSetModel).where(
                ParameterEnumSetModel.project_id == str(project_id)
            ).order_by(ParameterEnumSetModel.created_at)
            return [self._enum_record(item) for item in session.scalars(query)]

    def update_enum_set(
        self, enum_set_id: UUID, *, expected_version: int, name: str,
        description: str, options: Sequence[ParameterEnumOption],
    ) -> ParameterEnumSetRecord:
        normalized, validated = validate_enum_values(name, options)
        try:
            with self._session_factory() as session, session.begin():
                model = session.scalar(select(ParameterEnumSetModel).where(
                    ParameterEnumSetModel.id == str(enum_set_id)
                ).with_for_update())
                if model is None:
                    raise self._not_found(enum_set_id)
                current = self._enum_record(model)
                if current.state_version != expected_version:
                    raise ParameterEnumVersionConflict(
                        f"state version mismatch: expected {expected_version}, "
                        f"current {current.state_version}"
                    )
                self._validate_existing_defaults(session, enum_set_id, validated)
                updated = current.update(
                    name=normalized, description=description, options=validated
                )
                model.name = updated.name
                model.description = updated.description
                model.options = self._option_values(updated.options)
                model.state_version = updated.state_version
                model.updated_at = updated.updated_at
                session.flush()
                return updated
        except IntegrityError:
            raise ParameterEnumConflict(
                "an enum set with this project and name exists"
            ) from None

    def delete_enum_set(self, enum_set_id: UUID) -> None:
        try:
            with self._session_factory() as session, session.begin():
                model = session.scalar(select(ParameterEnumSetModel).where(
                    ParameterEnumSetModel.id == str(enum_set_id)
                ).with_for_update())
                if model is None:
                    raise self._not_found(enum_set_id)
                reference = session.scalar(select(AutomationScriptParameterModel).where(
                    AutomationScriptParameterModel.enum_set_id == str(enum_set_id)
                ).limit(1))
                if reference is not None:
                    raise ParameterEnumConflict(
                        "parameter enum is referenced by a script parameter"
                    )
                session.delete(model)
        except IntegrityError:
            raise ParameterEnumConflict(
                "parameter enum is referenced by a script parameter"
            ) from None

    def list_script_parameters(self, script_id: UUID) -> list[ScriptParameterDefinition]:
        with self._session_factory() as session:
            self._script(session, script_id)
            query = select(AutomationScriptParameterModel).where(
                AutomationScriptParameterModel.script_id == str(script_id)
            ).order_by(AutomationScriptParameterModel.position)
            return [self._parameter_record(item) for item in session.scalars(query)]

    def replace_script_parameters(
        self, script_id: UUID, definitions: Sequence[ScriptParameterDefinition]
    ) -> list[ScriptParameterDefinition]:
        validated = validate_parameter_definitions(definitions)
        with self._session_factory() as session, session.begin():
            script = self._script(session, script_id, lock=True)
            self._validate_parameter_enums(session, UUID(script.project_id), validated)
            session.execute(delete(AutomationScriptParameterModel).where(
                AutomationScriptParameterModel.script_id == str(script_id)
            ))
            for item in validated:
                session.add(AutomationScriptParameterModel(
                    script_id=str(script_id), name=item.name, label=item.label,
                    input_type=item.input_type.value, required=item.required,
                    enum_set_id=str(item.enum_set_id) if item.enum_set_id else None,
                    default_value=item.default_value, position=item.position,
                ))
            session.flush()
        return list(validated)

    def _validate_parameter_enums(
        self, session: Session, project_id: UUID,
        definitions: Sequence[ScriptParameterDefinition],
    ) -> None:
        for item in definitions:
            if item.enum_set_id is None:
                continue
            model = session.get(ParameterEnumSetModel, str(item.enum_set_id))
            if model is None:
                raise ScriptParameterConflict("parameter enum was not found")
            enum_set = self._enum_record(model)
            if enum_set.project_id != project_id:
                raise ScriptParameterConflict("parameter enum belongs to a different project")
            identities = {json_identity(option.value) for option in enum_set.options}
            if json_identity(item.default_value) not in identities:
                raise ScriptParameterConflict(
                    "enum default_value must match an enum option"
                )

    def _validate_existing_defaults(
        self, session: Session, enum_set_id: UUID,
        options: Sequence[ParameterEnumOption],
    ) -> None:
        identities = {json_identity(option.value) for option in options}
        query = select(AutomationScriptParameterModel).where(
            AutomationScriptParameterModel.enum_set_id == str(enum_set_id)
        )
        if any(
            json_identity(item.default_value) not in identities
            for item in session.scalars(query)
        ):
            raise ParameterEnumConflict(
                "enum options cannot invalidate a referenced default"
            )

    @staticmethod
    def _script(session: Session, script_id: UUID, lock: bool = False) -> AutomationScriptModel:
        query = select(AutomationScriptModel).where(
            AutomationScriptModel.id == str(script_id)
        )
        model = session.scalar(query.with_for_update() if lock else query)
        if model is None:
            raise ScriptNotFound(f"automation script {script_id} was not found")
        return model

    @classmethod
    def _enum_record(cls, model: ParameterEnumSetModel) -> ParameterEnumSetRecord:
        return ParameterEnumSetRecord(
            UUID(model.id), UUID(model.project_id), model.name, model.description,
            tuple(ParameterEnumOption(item["label"], item["value"]) for item in model.options),
            model.state_version, cls._utc(model.created_at), cls._utc(model.updated_at),
        )

    @staticmethod
    def _parameter_record(model: AutomationScriptParameterModel) -> ScriptParameterDefinition:
        return ScriptParameterDefinition(
            model.name, model.label, ParameterInputType(model.input_type), model.required,
            UUID(model.enum_set_id) if model.enum_set_id else None,
            model.default_value, model.position,
        )

    @staticmethod
    def _option_values(options: Sequence[ParameterEnumOption]) -> list[dict[str, object]]:
        return [{"label": item.label, "value": item.value} for item in options]

    @staticmethod
    def _not_found(enum_set_id: UUID) -> ParameterEnumNotFound:
        return ParameterEnumNotFound(f"parameter enum {enum_set_id} was not found")

    @staticmethod
    def _utc(value: datetime) -> datetime:
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)