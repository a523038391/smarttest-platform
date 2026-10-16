from copy import deepcopy
from datetime import datetime, timezone
from threading import RLock
from typing import Sequence
from uuid import UUID, uuid4

from .connector_domain import (
    ConnectorAuthType, ConnectorKind, ConnectorRecord, ConnectorTool,
)
from .environment_crypto import SecretEncryptor


class ConnectorNotFound(LookupError):
    pass


class ConnectorConflict(ValueError):
    pass


class ConnectorNameConflict(ConnectorConflict):
    pass


class ConnectorVersionConflict(ConnectorConflict):
    pass


class ConnectorCredentialRequired(ConnectorConflict):
    pass


def validate_connector_name(name: str) -> str:
    normalized = name.strip()
    if not normalized or len(normalized) > 255:
        raise ValueError("connector name must contain 1 to 255 characters")
    return normalized


def validate_credential(auth_type: ConnectorAuthType, credential: str | None) -> None:
    if auth_type is ConnectorAuthType.NONE:
        return
    if credential is None or not credential or len(credential) > 65_536:
        raise ConnectorCredentialRequired(
            "a non-empty credential is required for this auth type"
        )


class ConnectorRepository:
    def __init__(self, encryptor: SecretEncryptor | None = None) -> None:
        self._encryptor = encryptor or SecretEncryptor(None, None)
        self._lock = RLock()
        self._records: dict[UUID, ConnectorRecord] = {}
        self._names: dict[str, UUID] = {}

    def create_connector(
        self, *, name: str, description: str, kind: ConnectorKind, base_url: str,
        auth_type: ConnectorAuthType, auth_header: str, credential: str | None,
        enabled: bool, timeout_seconds: int,
    ) -> ConnectorRecord:
        normalized = validate_connector_name(name)
        validate_credential(auth_type, credential)
        connector_id, now = uuid4(), datetime.now(timezone.utc)
        encrypted = None
        if auth_type is not ConnectorAuthType.NONE:
            encrypted = self._encryptor.encrypt_connector(
                credential or "", connector_id=connector_id, secret_revision=1
            )
        record = ConnectorRecord(
            connector_id, normalized, description, kind, base_url, auth_type,
            auth_header, enabled, timeout_seconds, 0, 1 if encrypted else 0,
            encrypted, created_at=now, updated_at=now,
        )
        with self._lock:
            key = normalized.casefold()
            if key in self._names:
                raise ConnectorNameConflict("a connector with this name already exists")
            self._records[connector_id] = record
            self._names[key] = connector_id
        return deepcopy(record)

    def list_connectors(self) -> list[ConnectorRecord]:
        with self._lock:
            return deepcopy(sorted(self._records.values(), key=lambda item: item.created_at))

    def get_connector(self, connector_id: UUID) -> ConnectorRecord:
        with self._lock:
            try:
                return deepcopy(self._records[connector_id])
            except KeyError:
                raise ConnectorNotFound(f"connector {connector_id} was not found") from None

    def update_connector(
        self, connector_id: UUID, *, expected_version: int, name: str,
        description: str, kind: ConnectorKind, base_url: str,
        auth_type: ConnectorAuthType, auth_header: str, credential: str | None,
        credential_provided: bool, enabled: bool | None, timeout_seconds: int,
    ) -> ConnectorRecord:
        normalized = validate_connector_name(name)
        with self._lock:
            current = self.get_connector(connector_id)
            self._check_version(current, expected_version)
            owner = self._names.get(normalized.casefold())
            if owner is not None and owner != connector_id:
                raise ConnectorNameConflict("a connector with this name already exists")
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
            if normalized.casefold() != current.name.casefold():
                del self._names[current.name.casefold()]
                self._names[normalized.casefold()] = connector_id
            self._records[connector_id] = updated
            return deepcopy(updated)

    def set_enabled(
        self, connector_id: UUID, *, enabled: bool, expected_version: int
    ) -> ConnectorRecord:
        with self._lock:
            current = self.get_connector(connector_id)
            self._check_version(current, expected_version)
            updated = current.update(enabled=enabled)
            self._records[connector_id] = updated
            return deepcopy(updated)

    def record_test(
        self, connector_id: UUID, *, expected_version: int, success: bool,
        tools: Sequence[ConnectorTool], error: str | None,
    ) -> ConnectorRecord:
        with self._lock:
            current = self.get_connector(connector_id)
            self._check_version(current, expected_version)
            updated = current.update(
                tools=tuple(deepcopy(tools)), last_tested_at=datetime.now(timezone.utc),
                last_test_success=success, last_error=error,
            )
            self._records[connector_id] = updated
            return deepcopy(updated)

    def delete_connector(self, connector_id: UUID) -> None:
        with self._lock:
            current = self.get_connector(connector_id)
            del self._records[connector_id]
            del self._names[current.name.casefold()]

    def credential_for(self, record: ConnectorRecord) -> str | None:
        if record.credential is None:
            return None
        return self._encryptor.decrypt_connector(
            record.credential, connector_id=record.id,
            secret_revision=record.secret_revision,
        )

    @staticmethod
    def _check_version(record: ConnectorRecord, expected: int) -> None:
        if record.state_version != expected:
            raise ConnectorVersionConflict(
                f"state version mismatch: expected {expected}, current {record.state_version}"
            )