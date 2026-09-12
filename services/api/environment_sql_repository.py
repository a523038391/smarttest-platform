from datetime import datetime, timezone
from typing import Sequence
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from .environment_crypto import SecretEncryptor
from .environment_domain import (
    ConfigurationCategory,
    ConfigurationValue,
    ConfigurationValueInput,
    EncryptedSecret,
    EnvironmentRecord,
    EnvironmentStatus,
    validate_environment_transition,
)
from .environment_models import (
    EnvironmentModel,
    EnvironmentRevisionModel,
    EnvironmentValueModel,
)
from .environment_repository import (
    EnvironmentConflict,
    EnvironmentNotFound,
    EnvironmentVersionConflict,
    build_configuration_values,
    validate_environment_name,
)


class SqlEnvironmentRepository:
    def __init__(
        self, session_factory: sessionmaker[Session], encryptor: SecretEncryptor
    ) -> None:
        self._session_factory = session_factory
        self._encryptor = encryptor

    def create_environment(
        self,
        project_id: UUID,
        name: str,
        environment_variables: Sequence[ConfigurationValueInput],
        common_parameters: Sequence[ConfigurationValueInput],
    ) -> EnvironmentRecord:
        environment_id = uuid4()
        normalized_name = validate_environment_name(name)
        values = build_configuration_values(
            self._encryptor, project_id, environment_id, 1,
            environment_variables, common_parameters, None,
        )
        now = datetime.now(timezone.utc)
        record = EnvironmentRecord(
            environment_id, project_id, normalized_name, EnvironmentStatus.DRAFT,
            1, 0, values, now, now,
        )
        try:
            with self._session_factory() as session, session.begin():
                session.add(EnvironmentModel(
                    id=str(environment_id), project_id=str(project_id),
                    name=normalized_name, status=record.status.value,
                    current_revision=1, state_version=0,
                    created_at=now, updated_at=now,
                ))
                self._add_revision(session, record)
        except IntegrityError:
            raise EnvironmentConflict(
                "an environment with this project and name exists"
            ) from None
        return record

    def get_environment(self, environment_id: UUID) -> EnvironmentRecord:
        with self._session_factory() as session:
            model = session.get(EnvironmentModel, str(environment_id))
            if model is None:
                raise self._not_found(environment_id)
            return self._current_record(session, model)

    def list_environments(self, project_id: UUID) -> list[EnvironmentRecord]:
        with self._session_factory() as session:
            query = select(EnvironmentModel).where(
                EnvironmentModel.project_id == str(project_id)
            ).order_by(EnvironmentModel.created_at)
            return [self._current_record(session, model) for model in session.scalars(query)]

    def update_environment(
        self,
        environment_id: UUID,
        *,
        expected_version: int,
        name: str,
        status: EnvironmentStatus,
        environment_variables: Sequence[ConfigurationValueInput],
        common_parameters: Sequence[ConfigurationValueInput],
    ) -> EnvironmentRecord:
        normalized_name = validate_environment_name(name)
        try:
            with self._session_factory() as session, session.begin():
                model = session.scalar(select(EnvironmentModel).where(
                    EnvironmentModel.id == str(environment_id)
                ).with_for_update())
                if model is None:
                    raise self._not_found(environment_id)
                current = self._current_record(session, model)
                if current.state_version != expected_version:
                    raise EnvironmentVersionConflict(
                        f"state version mismatch: expected {expected_version}, "
                        f"current {current.state_version}"
                    )
                validate_environment_transition(current.status, status)
                values = build_configuration_values(
                    self._encryptor, current.project_id, current.id,
                    current.revision + 1, environment_variables,
                    common_parameters, current,
                )
                updated = current.update(
                    name=normalized_name, status=status, values=values
                )
                model.name = updated.name
                model.status = updated.status.value
                model.current_revision = updated.revision
                model.state_version = updated.state_version
                model.updated_at = updated.updated_at
                self._add_revision(session, updated)
                session.flush()
                return updated
        except IntegrityError:
            raise EnvironmentConflict(
                "an environment with this project and name exists"
            ) from None

    def list_revisions(self, environment_id: UUID) -> list[EnvironmentRecord]:
        with self._session_factory() as session:
            parent = session.get(EnvironmentModel, str(environment_id))
            if parent is None:
                raise self._not_found(environment_id)
            query = select(EnvironmentRevisionModel).where(
                EnvironmentRevisionModel.environment_id == str(environment_id)
            ).order_by(EnvironmentRevisionModel.revision)
            return [self._revision_record(session, parent, item) for item in session.scalars(query)]

    def get_revision(self, environment_id: UUID, revision: int) -> EnvironmentRecord:
        with self._session_factory() as session:
            parent = session.get(EnvironmentModel, str(environment_id))
            if parent is None:
                raise self._not_found(environment_id)
            model = session.get(
                EnvironmentRevisionModel, (str(environment_id), revision)
            )
            if model is None:
                raise EnvironmentNotFound(
                    f"environment {environment_id} revision {revision} was not found"
                )
            return self._revision_record(session, parent, model)

    def _current_record(
        self, session: Session, model: EnvironmentModel
    ) -> EnvironmentRecord:
        revision = session.get(
            EnvironmentRevisionModel, (model.id, model.current_revision)
        )
        if revision is None:
            raise EnvironmentNotFound("environment revision data was not found")
        values = self._values(session, model.id, model.current_revision)
        return EnvironmentRecord(
            UUID(model.id), UUID(model.project_id), model.name,
            EnvironmentStatus(model.status), model.current_revision,
            model.state_version, values, self._utc(model.created_at),
            self._utc(model.updated_at),
        )

    def _revision_record(
        self,
        session: Session,
        parent: EnvironmentModel,
        revision: EnvironmentRevisionModel,
    ) -> EnvironmentRecord:
        return EnvironmentRecord(
            UUID(parent.id), UUID(parent.project_id), revision.name,
            EnvironmentStatus(revision.status), revision.revision,
            revision.revision - 1, self._values(session, parent.id, revision.revision),
            self._utc(parent.created_at), self._utc(revision.created_at),
        )

    @staticmethod
    def _add_revision(session: Session, record: EnvironmentRecord) -> None:
        session.add(EnvironmentRevisionModel(
            environment_id=str(record.id), revision=record.revision,
            name=record.name, status=record.status.value, created_at=record.updated_at,
        ))
        positions: dict[ConfigurationCategory, int] = {
            category: 0 for category in ConfigurationCategory
        }
        for value in record.values:
            encrypted = value.encrypted
            session.add(EnvironmentValueModel(
                environment_id=str(record.id), revision=record.revision,
                category=value.category.value, name=value.name,
                position=positions[value.category],
                is_secret=value.secret,
                public_value=None if value.secret else value.value,
                secret_ref=str(value.secret_ref) if value.secret_ref else None,
                ciphertext=encrypted.ciphertext if encrypted else None,
                tag=encrypted.tag if encrypted else None,
                nonce=encrypted.nonce if encrypted else None,
                wrapped_dek=encrypted.wrapped_dek if encrypted else None,
                wrap_nonce=encrypted.wrap_nonce if encrypted else None,
                key_id=encrypted.key_id if encrypted else None,
                algorithm=encrypted.algorithm if encrypted else None,
            ))
            positions[value.category] += 1

    @staticmethod
    def _values(
        session: Session, environment_id: str, revision: int
    ) -> tuple[ConfigurationValue, ...]:
        query = select(EnvironmentValueModel).where(
            EnvironmentValueModel.environment_id == environment_id,
            EnvironmentValueModel.revision == revision,
        ).order_by(EnvironmentValueModel.category.desc(), EnvironmentValueModel.position)
        result: list[ConfigurationValue] = []
        for model in session.scalars(query):
            encrypted = None
            if model.is_secret:
                fields = (
                    model.ciphertext, model.tag, model.nonce, model.wrapped_dek,
                    model.wrap_nonce, model.key_id, model.algorithm,
                )
                if any(item is None for item in fields):
                    raise EnvironmentNotFound("environment secret data was not found")
                encrypted = EncryptedSecret(*fields)  # type: ignore[arg-type]
            result.append(ConfigurationValue(
                ConfigurationCategory(model.category), model.name, model.is_secret,
                value=None if model.is_secret else model.public_value,
                secret_ref=UUID(model.secret_ref) if model.secret_ref else None,
                encrypted=encrypted,
            ))
        return tuple(result)

    @staticmethod
    def _not_found(environment_id: UUID) -> EnvironmentNotFound:
        return EnvironmentNotFound(f"environment {environment_id} was not found")

    @staticmethod
    def _utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)