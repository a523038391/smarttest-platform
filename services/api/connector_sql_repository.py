from datetime import datetime, timezone
from typing import Sequence
from uuid import UUID, uuid4

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from .connector_domain import ConnectorAuthType, ConnectorKind, ConnectorRecord, ConnectorTool
from .connector_models import ConnectorModel
from .connector_repository import (
    ConnectorCredentialRequired, ConnectorNameConflict, ConnectorNotFound,
    ConnectorVersionConflict, validate_connector_name, validate_credential,
)
from .environment_crypto import SecretEncryptor
from .environment_domain import EncryptedSecret


class SqlConnectorRepository:
    def __init__(self, session_factory: sessionmaker[Session], encryptor: SecretEncryptor) -> None:
        self._session_factory = session_factory
        self._encryptor = encryptor

    def create_connector(self, *, name: str, description: str, kind: ConnectorKind,
                         base_url: str, auth_type: ConnectorAuthType, auth_header: str,
                         credential: str | None, enabled: bool,
                         timeout_seconds: int) -> ConnectorRecord:
        normalized = validate_connector_name(name)
        validate_credential(auth_type, credential)
        connector_id, now = uuid4(), datetime.now(timezone.utc)
        encrypted = None if auth_type is ConnectorAuthType.NONE else self._encryptor.encrypt_connector(
            credential or "", connector_id=connector_id, secret_revision=1
        )
        record = ConnectorRecord(
            connector_id, normalized, description, kind, base_url, auth_type, auth_header,
            enabled, timeout_seconds, 0, 1 if encrypted else 0, encrypted,
            created_at=now, updated_at=now,
        )
        try:
            with self._session_factory() as session, session.begin():
                if self._name_owner(session, normalized) is not None:
                    raise ConnectorNameConflict(
                        "a connector with this name already exists"
                    )
                session.add(self._model(record))
        except IntegrityError:
            raise ConnectorNameConflict("a connector with this name already exists") from None
        return record

    def list_connectors(self) -> list[ConnectorRecord]:
        with self._session_factory() as session:
            return [self._record(item) for item in session.scalars(
                select(ConnectorModel).order_by(ConnectorModel.created_at)
            )]

    def get_connector(self, connector_id: UUID) -> ConnectorRecord:
        with self._session_factory() as session:
            model = session.get(ConnectorModel, str(connector_id))
            if model is None:
                raise ConnectorNotFound(f"connector {connector_id} was not found")
            return self._record(model)

    def update_connector(self, connector_id: UUID, *, expected_version: int, name: str,
                         description: str, kind: ConnectorKind, base_url: str,
                         auth_type: ConnectorAuthType, auth_header: str,
                         credential: str | None, credential_provided: bool,
                         enabled: bool | None, timeout_seconds: int) -> ConnectorRecord:
        normalized = validate_connector_name(name)
        try:
            with self._session_factory() as session, session.begin():
                model = self._locked(session, connector_id)
                current = self._record(model)
                self._check_version(current, expected_version)
                owner = self._name_owner(session, normalized)
                if owner is not None and owner != str(connector_id):
                    raise ConnectorNameConflict(
                        "a connector with this name already exists"
                    )
                encrypted, revision = current.credential, current.secret_revision
                if auth_type is ConnectorAuthType.NONE:
                    encrypted, revision = None, revision + int(encrypted is not None)
                elif credential_provided:
                    validate_credential(auth_type, credential)
                    revision += 1
                    encrypted = self._encryptor.encrypt_connector(
                        credential or "", connector_id=connector_id, secret_revision=revision
                    )
                elif encrypted is None:
                    raise ConnectorCredentialRequired(
                        "a credential is required for this auth type"
                    )
                updated = current.update(
                    name=normalized, description=description, kind=kind, base_url=base_url,
                    auth_type=auth_type, auth_header=auth_header, credential=encrypted,
                    secret_revision=revision,
                    enabled=current.enabled if enabled is None else enabled,
                    timeout_seconds=timeout_seconds,
                )
                self._apply(model, updated)
                session.flush()
                return updated
        except IntegrityError:
            raise ConnectorNameConflict("a connector with this name already exists") from None

    def set_enabled(self, connector_id: UUID, *, enabled: bool,
                    expected_version: int) -> ConnectorRecord:
        with self._session_factory() as session, session.begin():
            model = self._locked(session, connector_id)
            current = self._record(model)
            self._check_version(current, expected_version)
            updated = current.update(enabled=enabled)
            self._apply(model, updated)
            return updated

    def record_test(self, connector_id: UUID, *, expected_version: int, success: bool,
                    tools: Sequence[ConnectorTool], error: str | None) -> ConnectorRecord:
        with self._session_factory() as session, session.begin():
            model = self._locked(session, connector_id)
            current = self._record(model)
            self._check_version(current, expected_version)
            updated = current.update(
                tools=tuple(tools), last_tested_at=datetime.now(timezone.utc),
                last_test_success=success, last_error=error,
            )
            self._apply(model, updated)
            return updated

    def delete_connector(self, connector_id: UUID) -> None:
        with self._session_factory() as session, session.begin():
            result = session.execute(delete(ConnectorModel).where(
                ConnectorModel.id == str(connector_id)
            ))
            if not result.rowcount:
                raise ConnectorNotFound(f"connector {connector_id} was not found")

    def credential_for(self, record: ConnectorRecord) -> str | None:
        if record.credential is None:
            return None
        return self._encryptor.decrypt_connector(
            record.credential, connector_id=record.id,
            secret_revision=record.secret_revision,
        )

    def _locked(self, session: Session, connector_id: UUID) -> ConnectorModel:
        model = session.scalar(select(ConnectorModel).where(
            ConnectorModel.id == str(connector_id)
        ).with_for_update())
        if model is None:
            raise ConnectorNotFound(f"connector {connector_id} was not found")
        return model

    @staticmethod
    def _name_owner(session: Session, name: str) -> str | None:
        return session.scalar(select(ConnectorModel.id).where(
            func.lower(ConnectorModel.name) == name.lower()
        ))

    @staticmethod
    def _check_version(record: ConnectorRecord, expected: int) -> None:
        if record.state_version != expected:
            raise ConnectorVersionConflict(
                f"state version mismatch: expected {expected}, current {record.state_version}"
            )

    @staticmethod
    def _utc(value: datetime | None) -> datetime | None:
        if value is None or value.tzinfo is not None:
            return value
        return value.replace(tzinfo=timezone.utc)

    @classmethod
    def _record(cls, model: ConnectorModel) -> ConnectorRecord:
        fields = (model.ciphertext, model.tag, model.nonce, model.wrapped_dek,
                  model.wrap_nonce, model.key_id, model.algorithm)
        encrypted = None
        if any(item is not None for item in fields):
            if any(item is None for item in fields):
                raise ConnectorNotFound("connector credential data was not found")
            encrypted = EncryptedSecret(*fields)  # type: ignore[arg-type]
        tools = tuple(ConnectorTool(
            str(item["name"]), str(item.get("description", "")),
            item.get("input_schema", {}) if isinstance(item.get("input_schema", {}), dict) else {},
        ) for item in model.tools)
        return ConnectorRecord(
            UUID(model.id), model.name, model.description, ConnectorKind(model.kind),
            model.base_url, ConnectorAuthType(model.auth_type), model.auth_header,
            model.enabled, model.timeout_seconds, model.state_version,
            model.secret_revision, encrypted, tools, cls._utc(model.last_tested_at),
            model.last_test_success, model.last_error, cls._utc(model.created_at),
            cls._utc(model.updated_at),
        )  # type: ignore[arg-type]

    @staticmethod
    def _model(record: ConnectorRecord) -> ConnectorModel:
        model = ConnectorModel(id=str(record.id))
        SqlConnectorRepository._apply(model, record)
        return model

    @staticmethod
    def _apply(model: ConnectorModel, record: ConnectorRecord) -> None:
        model.name, model.description = record.name, record.description
        model.kind, model.base_url = record.kind.value, record.base_url
        model.auth_type, model.auth_header = record.auth_type.value, record.auth_header
        model.enabled, model.timeout_seconds = record.enabled, record.timeout_seconds
        model.state_version, model.secret_revision = record.state_version, record.secret_revision
        encrypted = record.credential
        model.ciphertext = encrypted.ciphertext if encrypted else None
        model.tag = encrypted.tag if encrypted else None
        model.nonce = encrypted.nonce if encrypted else None
        model.wrapped_dek = encrypted.wrapped_dek if encrypted else None
        model.wrap_nonce = encrypted.wrap_nonce if encrypted else None
        model.key_id = encrypted.key_id if encrypted else None
        model.algorithm = encrypted.algorithm if encrypted else None
        model.tools = [{"name": tool.name, "description": tool.description,
                        "input_schema": tool.input_schema} for tool in record.tools]
        model.last_tested_at, model.last_test_success = record.last_tested_at, record.last_test_success
        model.last_error, model.created_at, model.updated_at = (
            record.last_error, record.created_at, record.updated_at,
        )