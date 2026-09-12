from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from secrets import token_urlsafe
from threading import RLock
from typing import Protocol
from uuid import UUID, uuid4

from packages.protocol import SecretReference, TaskEnvelope
from services.api.domain import AttemptRecord, AttemptState
from services.api.environment_repository import (
    ResolvedSecretValue,
    canonical_secret_references,
)


REJECTION_MESSAGE = "secret capability was rejected"
ACTIVE_SECRET_STATES = frozenset({AttemptState.PREPARING, AttemptState.RUNNING})


class CapabilityRejected(PermissionError):
    def __init__(self) -> None:
        super().__init__(REJECTION_MESSAGE)


@dataclass(frozen=True, slots=True)
class SecretCapability:
    token: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class SecretGrantBinding:
    task_id: UUID
    tenant_id: UUID
    project_id: UUID
    run_id: UUID
    attempt_id: UUID
    runner_id: str
    environment_id: UUID
    environment_revision: int
    secret_references: tuple[SecretReference, ...]


@dataclass(frozen=True, slots=True)
class SecretGrantRecord:
    id: UUID
    token_digest: str = field(repr=False)
    binding: SecretGrantBinding
    issued_at: datetime
    expires_at: datetime
    consumed_at: datetime | None = None
    revoked_at: datetime | None = None


class AttemptReader(Protocol):
    def get_attempt(self, attempt_id: UUID) -> AttemptRecord: ...


class SecretResolver(Protocol):
    def validate_secret_references(
        self, project_id: UUID, environment_id: UUID, revision: int,
        secret_references: Sequence[SecretReference],
    ) -> tuple[SecretReference, ...]: ...

    def resolve_secret_values(
        self, project_id: UUID, environment_id: UUID, revision: int,
        secret_references: Sequence[SecretReference],
    ) -> tuple[ResolvedSecretValue, ...]: ...


class SecretGrantStore(Protocol):
    def issue(self, record: SecretGrantRecord, now: datetime) -> SecretGrantRecord: ...
    def consume(
        self, token_digest: str, binding: SecretGrantBinding, now: datetime
    ) -> SecretGrantRecord: ...
    def revoke(self, token_digest: str, now: datetime) -> None: ...
    def revoke_attempt(self, attempt_id: UUID, now: datetime) -> int: ...


class InMemorySecretGrantStore:
    def __init__(self, attempts: AttemptReader) -> None:
        self._attempts = attempts
        self._records: dict[str, SecretGrantRecord] = {}
        self._lock = RLock()

    def issue(self, record: SecretGrantRecord, now: datetime) -> SecretGrantRecord:
        with self._lock:
            attempt = self._valid_attempt(record.binding, now)
            lease_expires_at = attempt.lease_expires_at
            if lease_expires_at is None:
                raise CapabilityRejected()
            expires_at = min(record.expires_at, lease_expires_at)
            if expires_at <= now or record.token_digest in self._records:
                raise CapabilityRejected()
            stored = replace(record, expires_at=expires_at)
            self._records[record.token_digest] = stored
            return stored

    def consume(
        self, token_digest: str, binding: SecretGrantBinding, now: datetime
    ) -> SecretGrantRecord:
        with self._lock:
            self._valid_attempt(binding, now)
            record = self._records.get(token_digest)
            if (
                record is None or record.binding != binding or record.expires_at <= now
                or record.consumed_at is not None or record.revoked_at is not None
            ):
                raise CapabilityRejected()
            consumed = replace(record, consumed_at=now)
            self._records[token_digest] = consumed
            return consumed

    def revoke(self, token_digest: str, now: datetime) -> None:
        with self._lock:
            record = self._records.get(token_digest)
            if (
                record is not None
                and record.consumed_at is None
                and record.revoked_at is None
            ):
                self._records[token_digest] = replace(record, revoked_at=now)

    def revoke_attempt(self, attempt_id: UUID, now: datetime) -> int:
        count = 0
        with self._lock:
            for digest, record in tuple(self._records.items()):
                if (
                    record.binding.attempt_id == attempt_id
                    and record.consumed_at is None and record.revoked_at is None
                ):
                    self._records[digest] = replace(record, revoked_at=now)
                    count += 1
        return count

    def _valid_attempt(self, binding: SecretGrantBinding, now: datetime) -> AttemptRecord:
        try:
            attempt = self._attempts.get_attempt(binding.attempt_id)
        except Exception:
            raise CapabilityRejected() from None
        if (
            attempt.run_id != binding.run_id or attempt.runner_id != binding.runner_id
            or attempt.state not in ACTIVE_SECRET_STATES
            or attempt.lease_expires_at is None or attempt.lease_expires_at <= now
        ):
            raise CapabilityRejected()
        return attempt


InMemorySecretCapabilityStore = InMemorySecretGrantStore


class SecretBroker:
    def __init__(
        self,
        store: SecretGrantStore,
        resolver: SecretResolver,
        *,
        ttl_seconds: int = 60,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("capability TTL must be positive")
        self._store = store
        self._resolver = resolver
        self._ttl = timedelta(seconds=ttl_seconds)
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def issue(self, task: TaskEnvelope, runner_id: str) -> SecretCapability:
        if (
            not task.secret_references or task.environment_id is None
            or task.environment_revision is None or not runner_id
        ):
            raise CapabilityRejected()
        try:
            references = self._resolver.validate_secret_references(
                task.project_id, task.environment_id, task.environment_revision,
                task.secret_references,
            )
        except Exception:
            raise CapabilityRejected() from None
        now = self._clock()
        binding = self._binding(task, runner_id, references)
        token = token_urlsafe(32)
        record = SecretGrantRecord(
            id=uuid4(), token_digest=self.digest(token), binding=binding,
            issued_at=now, expires_at=min(now + self._ttl, task.deadline),
        )
        try:
            self._store.issue(record, now)
        except Exception:
            raise CapabilityRejected() from None
        return SecretCapability(token)

    def redeem(
        self, capability: SecretCapability, task: TaskEnvelope, runner_id: str
    ) -> tuple[ResolvedSecretValue, ...]:
        try:
            binding = self._binding(
                task, runner_id, canonical_secret_references(task.secret_references)
            )
            self._store.consume(self.digest(capability.token), binding, self._clock())
        except Exception:
            raise CapabilityRejected() from None
        try:
            return self._resolver.resolve_secret_values(
                binding.project_id, binding.environment_id,
                binding.environment_revision, binding.secret_references,
            )
        except Exception:
            raise CapabilityRejected() from None

    def revoke(self, capability: SecretCapability) -> None:
        try:
            self._store.revoke(self.digest(capability.token), self._clock())
        except Exception:
            raise CapabilityRejected() from None

    def revoke_attempt(self, attempt_id: UUID) -> int:
        try:
            return self._store.revoke_attempt(attempt_id, self._clock())
        except Exception:
            raise CapabilityRejected() from None

    @staticmethod
    def digest(token: str) -> str:
        return sha256(token.encode("utf-8")).hexdigest()

    @staticmethod
    def _binding(
        task: TaskEnvelope,
        runner_id: str,
        references: tuple[SecretReference, ...],
    ) -> SecretGrantBinding:
        if task.environment_id is None or task.environment_revision is None:
            raise CapabilityRejected()
        return SecretGrantBinding(
            task_id=task.task_id, tenant_id=task.tenant_id,
            project_id=task.project_id, run_id=task.run_id,
            attempt_id=task.attempt_id, runner_id=runner_id,
            environment_id=task.environment_id,
            environment_revision=task.environment_revision,
            secret_references=references,
        )


Capability = SecretCapability
GrantBinding = SecretGrantBinding
GrantRecord = SecretGrantRecord
InMemorySecretGrantRepository = InMemorySecretGrantStore