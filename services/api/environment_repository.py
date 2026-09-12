import json
import re
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import RLock
from typing import Protocol, Sequence
from uuid import UUID, uuid4

from packages.protocol import SecretReference

from .environment_crypto import SecretEncryptor
from .environment_domain import (
    ConfigurationCategory,
    ConfigurationSnapshot,
    ConfigurationValue,
    ConfigurationValueInput,
    EnvironmentRecord,
    EnvironmentStatus,
    validate_environment_transition,
)


MAX_VALUES_PER_CATEGORY = 100
MAX_VALUE_BYTES = 65_536
KEY_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]{0,127}$")
ENV_KEY_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")


class EnvironmentNotFound(LookupError):
    pass


class EnvironmentConflict(ValueError):
    pass


class EnvironmentVersionConflict(EnvironmentConflict):
    pass


class EnvironmentReader(Protocol):
    def get_revision(self, environment_id: UUID, revision: int) -> EnvironmentRecord: ...


@dataclass(frozen=True, slots=True)
class ResolvedSecretValue:
    category: ConfigurationCategory
    name: str
    secret_ref: UUID
    value: str = field(repr=False)


def canonical_secret_references(
    references: Sequence[SecretReference],
) -> tuple[SecretReference, ...]:
    identities = [(item.category, item.name, item.secret_ref) for item in references]
    if len(identities) != len(set(identities)):
        raise EnvironmentConflict("secret references must be unique")
    return tuple(sorted(
        references,
        key=lambda item: (item.category, item.name, str(item.secret_ref)),
    ))


def validate_environment_name(name: str) -> str:
    normalized = name.strip()
    if not normalized or len(normalized) > 255:
        raise ValueError("environment name must contain 1 to 255 characters")
    return normalized


def validate_inputs(
    category: ConfigurationCategory,
    values: Sequence[ConfigurationValueInput],
) -> None:
    if len(values) > MAX_VALUES_PER_CATEGORY:
        raise ValueError(f"at most {MAX_VALUES_PER_CATEGORY} values are allowed per category")
    names: set[str] = set()
    pattern = ENV_KEY_PATTERN if category is ConfigurationCategory.ENVIRONMENT_VARIABLE else KEY_PATTERN
    for item in values:
        if not pattern.fullmatch(item.name):
            raise ValueError(f"invalid configuration key: {item.name!r}")
        if item.name in names:
            raise ValueError(f"duplicate configuration key: {item.name}")
        names.add(item.name)
        if item.secret:
            if item.secret_ref is None and not isinstance(item.value, str):
                raise ValueError("secret values must be strings")
            if item.secret_ref is not None and item.value is not None:
                raise ValueError("secret value and secret_ref are mutually exclusive")
        else:
            if item.secret_ref is not None:
                raise ValueError("public values cannot use secret_ref")
            if category is ConfigurationCategory.ENVIRONMENT_VARIABLE and not isinstance(
                item.value, str
            ):
                raise ValueError("environment variable values must be strings")
        try:
            encoded = json.dumps(
                item.value, ensure_ascii=False, allow_nan=False, separators=(",", ":")
            ).encode("utf-8")
        except (TypeError, ValueError):
            raise ValueError("configuration values must be JSON serializable") from None
        if len(encoded) > MAX_VALUE_BYTES:
            raise ValueError(f"configuration values cannot exceed {MAX_VALUE_BYTES} bytes")


def build_configuration_values(
    encryptor: SecretEncryptor,
    project_id: UUID,
    environment_id: UUID,
    revision: int,
    environment_variables: Sequence[ConfigurationValueInput],
    common_parameters: Sequence[ConfigurationValueInput],
    previous: EnvironmentRecord | None,
) -> tuple[ConfigurationValue, ...]:
    result: list[ConfigurationValue] = []
    for category, inputs in (
        (ConfigurationCategory.ENVIRONMENT_VARIABLE, environment_variables),
        (ConfigurationCategory.COMMON_PARAMETER, common_parameters),
    ):
        validate_inputs(category, inputs)
        for item in inputs:
            if not item.secret:
                result.append(ConfigurationValue(category, item.name, False, deepcopy(item.value)))
                continue
            plaintext = item.value
            if item.secret_ref is not None:
                if previous is None:
                    raise EnvironmentConflict("secret_ref cannot be used during creation")
                source = next((
                    value for value in previous.values
                    if value.category is category and value.name == item.name
                    and value.secret_ref == item.secret_ref and value.secret
                ), None)
                if source is None or source.encrypted is None:
                    raise EnvironmentConflict("secret reference does not match the current revision")
                plaintext = encryptor.decrypt(
                    source.encrypted, project_id=previous.project_id,
                    environment_id=previous.id, revision=previous.revision,
                    category=source.category, name=source.name,
                )
            encrypted = encryptor.encrypt(
                plaintext, project_id=project_id, environment_id=environment_id,
                revision=revision, category=category, name=item.name,
            )
            result.append(ConfigurationValue(
                category, item.name, True, secret_ref=uuid4(), encrypted=encrypted
            ))
    return tuple(result)


class EnvironmentRepository:
    def __init__(self, encryptor: SecretEncryptor | None = None) -> None:
        self._encryptor = encryptor or SecretEncryptor(None, None)
        self._lock = RLock()
        self._environments: dict[UUID, EnvironmentRecord] = {}
        self._revisions: dict[UUID, list[EnvironmentRecord]] = {}
        self._names: dict[tuple[UUID, str], UUID] = {}

    def create_environment(
        self,
        project_id: UUID,
        name: str,
        environment_variables: Sequence[ConfigurationValueInput],
        common_parameters: Sequence[ConfigurationValueInput],
    ) -> EnvironmentRecord:
        normalized_name = validate_environment_name(name)
        environment_id = uuid4()
        with self._lock:
            key = (project_id, normalized_name)
            if key in self._names:
                raise EnvironmentConflict("an environment with this project and name exists")
            values = build_configuration_values(
                self._encryptor,
                project_id, environment_id, 1, environment_variables,
                common_parameters, None,
            )
            now = datetime.now(timezone.utc)
            record = EnvironmentRecord(
                environment_id, project_id, normalized_name, EnvironmentStatus.DRAFT,
                1, 0, values, now, now,
            )
            self._environments[environment_id] = record
            self._revisions[environment_id] = [record]
            self._names[key] = environment_id
            return deepcopy(record)

    def get_environment(self, environment_id: UUID) -> EnvironmentRecord:
        with self._lock:
            try:
                return deepcopy(self._environments[environment_id])
            except KeyError:
                raise EnvironmentNotFound(
                    f"environment {environment_id} was not found"
                ) from None

    def list_environments(self, project_id: UUID) -> list[EnvironmentRecord]:
        with self._lock:
            records = [
                item for item in self._environments.values()
                if item.project_id == project_id
            ]
            return deepcopy(sorted(records, key=lambda item: item.created_at))

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
        with self._lock:
            current = self.get_environment(environment_id)
            if current.state_version != expected_version:
                raise EnvironmentVersionConflict(
                    f"state version mismatch: expected {expected_version}, "
                    f"current {current.state_version}"
                )
            validate_environment_transition(current.status, status)
            owner = self._names.get((current.project_id, normalized_name))
            if owner is not None and owner != environment_id:
                raise EnvironmentConflict("an environment with this project and name exists")
            values = build_configuration_values(
                self._encryptor,
                current.project_id, current.id, current.revision + 1,
                environment_variables, common_parameters, current,
            )
            updated = current.update(name=normalized_name, status=status, values=values)
            if normalized_name != current.name:
                del self._names[(current.project_id, current.name)]
                self._names[(current.project_id, normalized_name)] = environment_id
            self._environments[environment_id] = updated
            self._revisions[environment_id].append(updated)
            return deepcopy(updated)

    def list_revisions(self, environment_id: UUID) -> list[EnvironmentRecord]:
        with self._lock:
            self.get_environment(environment_id)
            return deepcopy(self._revisions[environment_id])

    def get_revision(self, environment_id: UUID, revision: int) -> EnvironmentRecord:
        with self._lock:
            self.get_environment(environment_id)
            if revision < 1 or revision > len(self._revisions[environment_id]):
                raise EnvironmentNotFound(
                    f"environment {environment_id} revision {revision} was not found"
                )
            return deepcopy(self._revisions[environment_id][revision - 1])

class ConfigurationResolver:
    def __init__(self, repository: EnvironmentReader, encryptor: SecretEncryptor) -> None:
        self._repository = repository
        self._encryptor = encryptor

    def snapshot(
        self,
        project_id: UUID,
        environment_id: UUID,
        revision: int,
        secret_references: Sequence[SecretReference] = (),
    ) -> ConfigurationSnapshot:
        record = self._repository.get_revision(environment_id, revision)
        if record.project_id != project_id:
            raise EnvironmentNotFound(f"environment {environment_id} was not found")
        if record.status is not EnvironmentStatus.ACTIVE:
            raise EnvironmentConflict("only an active environment revision can be resolved")
        requested = {
            (ConfigurationCategory(item.category), item.name, item.secret_ref)
            for item in secret_references
        }
        variables: dict[str, str] = {}
        parameters: dict[str, object] = {}
        matched: set[tuple[ConfigurationCategory, str, UUID]] = set()
        for value in record.values:
            target = variables if value.category is ConfigurationCategory.ENVIRONMENT_VARIABLE else parameters
            if not value.secret:
                target[value.name] = deepcopy(value.value)  # type: ignore[assignment]
                continue
            identity = (value.category, value.name, value.secret_ref)
            if identity not in requested:
                continue
            if value.encrypted is None or value.secret_ref is None:
                raise EnvironmentConflict("secret reference is invalid")
            target[value.name] = self._encryptor.decrypt(
                value.encrypted, project_id=record.project_id,
                environment_id=record.id, revision=record.revision,
                category=value.category, name=value.name,
            )
            matched.add(identity)  # type: ignore[arg-type]
        if matched != requested:
            raise EnvironmentConflict("secret reference does not match the pinned revision")
        return ConfigurationSnapshot(record.id, record.revision, variables, parameters)

    def validate_secret_references(
        self,
        project_id: UUID,
        environment_id: UUID,
        revision: int,
        secret_references: Sequence[SecretReference],
    ) -> tuple[SecretReference, ...]:
        record, canonical = self._validated_record(
            project_id, environment_id, revision, secret_references
        )
        available = {
            (value.category.value, value.name, value.secret_ref)
            for value in record.values
            if value.secret and value.secret_ref is not None and value.encrypted is not None
        }
        requested = {
            (item.category, item.name, item.secret_ref) for item in canonical
        }
        if (
            not requested.issubset(available)
            or len(requested) != len(canonical)
        ):
            raise EnvironmentConflict("secret reference does not match the pinned revision")
        return canonical

    def resolve_secret_values(
        self,
        project_id: UUID,
        environment_id: UUID,
        revision: int,
        secret_references: Sequence[SecretReference],
    ) -> tuple[ResolvedSecretValue, ...]:
        record, canonical = self._validated_record(
            project_id, environment_id, revision, secret_references
        )
        values = {
            (value.category.value, value.name, value.secret_ref): value
            for value in record.values
            if value.secret and value.secret_ref is not None and value.encrypted is not None
        }
        if any(
            (item.category, item.name, item.secret_ref) not in values
            for item in canonical
        ):
            raise EnvironmentConflict("secret reference does not match the pinned revision")
        resolved: list[ResolvedSecretValue] = []
        for reference in canonical:
            value = values[(reference.category, reference.name, reference.secret_ref)]
            resolved.append(ResolvedSecretValue(
                category=value.category,
                name=value.name,
                secret_ref=reference.secret_ref,
                value=self._encryptor.decrypt(
                    value.encrypted,  # type: ignore[arg-type]
                    project_id=record.project_id,
                    environment_id=record.id,
                    revision=record.revision,
                    category=value.category,
                    name=value.name,
                ),
            ))
        return tuple(resolved)

    def _validated_record(
        self,
        project_id: UUID,
        environment_id: UUID,
        revision: int,
        secret_references: Sequence[SecretReference],
    ) -> tuple[EnvironmentRecord, tuple[SecretReference, ...]]:
        record = self._repository.get_revision(environment_id, revision)
        if record.project_id != project_id:
            raise EnvironmentNotFound(f"environment {environment_id} was not found")
        if record.status is not EnvironmentStatus.ACTIVE:
            raise EnvironmentConflict("only an active environment revision can be resolved")
        return record, canonical_secret_references(secret_references)