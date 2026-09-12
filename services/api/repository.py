import hashlib
import json
from copy import deepcopy
from datetime import datetime, timezone
from threading import RLock
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from packages.protocol import Engine, EventEnvelope, ResultEnvelope

from .domain import (
    ATTEMPT_LEASED,
    RUN_TERMINAL,
    ArtifactRecord,
    AttemptRecord,
    AttemptState,
    EventRecord,
    RunRecord,
    RunState,
)

if TYPE_CHECKING:
    from .sql_repository import SqlRunRepository


class RunNotFound(LookupError):
    pass


class IdempotencyConflict(ValueError):
    pass


class TerminalRunConflict(ValueError):
    pass


class AttemptNotFound(LookupError):
    pass


class AttemptConflict(ValueError):
    pass


class EventConflict(ValueError):
    pass


class ArtifactNotFound(LookupError):
    pass


class ArtifactConflict(ValueError):
    pass


class RunRepository:
    def __init__(self) -> None:
        self._lock = RLock()
        self._runs: dict[UUID, RunRecord] = {}
        self._idempotency: dict[str, tuple[str, UUID]] = {}
        self._attempts: dict[UUID, AttemptRecord] = {}
        self._events: dict[UUID, EventRecord] = {}
        self._event_sequences: dict[tuple[UUID, int], UUID] = {}
        self._artifacts: dict[UUID, ArtifactRecord] = {}
        self._next_cursor = 1

    @staticmethod
    def _fingerprint(engine: Engine, parameters: dict[str, Any]) -> str:
        canonical_request = json.dumps(
            {"engine": engine.value, "parameters": parameters},
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical_request.encode("utf-8")).hexdigest()

    def create(
        self,
        engine: Engine,
        parameters: dict[str, Any],
        idempotency_key: str | None = None,
    ) -> RunRecord:
        run, _ = self.create_or_get(engine, parameters, idempotency_key)
        return run

    def create_or_get(
        self,
        engine: Engine,
        parameters: dict[str, Any],
        idempotency_key: str | None = None,
    ) -> tuple[RunRecord, bool]:
        key = idempotency_key.strip() if idempotency_key else None
        fingerprint = self._fingerprint(engine, parameters)
        with self._lock:
            if key and key in self._idempotency:
                saved_fingerprint, run_id = self._idempotency[key]
                if saved_fingerprint != fingerprint:
                    raise IdempotencyConflict("idempotency key was used for another request")
                return deepcopy(self._runs[run_id]), True

            now = datetime.now(timezone.utc)
            run = RunRecord(uuid4(), engine, deepcopy(parameters), RunState.CREATED, now, now)
            self._runs[run.id] = run
            if key:
                self._idempotency[key] = (fingerprint, run.id)
            return deepcopy(run), False

    def list(self) -> list[RunRecord]:
        with self._lock:
            return deepcopy(sorted(self._runs.values(), key=lambda run: run.created_at))

    def get(self, run_id: UUID) -> RunRecord:
        with self._lock:
            try:
                return deepcopy(self._runs[run_id])
            except KeyError as exc:
                raise RunNotFound(f"run {run_id} was not found") from exc

    def transition(self, run_id: UUID, target: RunState) -> RunRecord:
        with self._lock:
            current = self._get_locked(run_id)
            updated = current.with_state(target)
            self._runs[run_id] = updated
            return deepcopy(updated)

    def cancel(self, run_id: UUID) -> RunRecord:
        with self._lock:
            current = self._get_locked(run_id)
            if current.state in RUN_TERMINAL:
                raise TerminalRunConflict(f"run is already {current.state.value}")
            if current.state is RunState.CANCELLING:
                return deepcopy(current)
            updated = current.with_state(RunState.CANCELLING)
            self._runs[run_id] = updated
            return deepcopy(updated)

    def create_attempt(
        self, run_id: UUID, attempt_id: UUID | None = None
    ) -> AttemptRecord:
        with self._lock:
            self._get_locked(run_id)
            resolved_id = attempt_id or uuid4()
            if resolved_id in self._attempts:
                raise AttemptConflict(f"attempt {resolved_id} already exists")
            now = datetime.now(timezone.utc)
            attempt = AttemptRecord(
                resolved_id, run_id, AttemptState.PENDING, now, now
            )
            self._attempts[resolved_id] = attempt
            return deepcopy(attempt)

    def begin_attempt(
        self,
        run_id: UUID,
        attempt_id: UUID,
        runner_id: str,
        lease_seconds: int,
    ) -> tuple[RunRecord, AttemptRecord]:
        with self._lock:
            run = self._get_locked(run_id)
            if attempt_id in self._attempts:
                raise AttemptConflict(f"attempt {attempt_id} already exists")
            running = run.with_state(RunState.RUNNING)
            now = datetime.now(timezone.utc)
            pending = AttemptRecord(
                attempt_id, run_id, AttemptState.PENDING, now, now
            )
            claimed = pending.claimed(runner_id, lease_seconds, now)
            self._runs[run_id] = running
            self._attempts[attempt_id] = claimed
            return deepcopy(running), deepcopy(claimed)

    def get_attempt(self, attempt_id: UUID) -> AttemptRecord:
        with self._lock:
            return deepcopy(self._get_attempt_locked(attempt_id))

    def list_attempts(self, run_id: UUID) -> list[AttemptRecord]:
        with self._lock:
            self._get_locked(run_id)
            attempts = [item for item in self._attempts.values() if item.run_id == run_id]
            return deepcopy(sorted(attempts, key=lambda item: (item.created_at, item.id)))

    def transition_attempt(
        self, attempt_id: UUID, target: AttemptState
    ) -> AttemptRecord:
        with self._lock:
            current = self._get_attempt_locked(attempt_id)
            updated = current.with_state(target)
            self._attempts[attempt_id] = updated
            return deepcopy(updated)

    def claim_attempt(
        self,
        attempt_id: UUID,
        runner_id: str,
        lease_seconds: int,
        now: datetime | None = None,
    ) -> AttemptRecord:
        heartbeat = now or datetime.now(timezone.utc)
        with self._lock:
            current = self._get_attempt_locked(attempt_id)
            updated = current.claimed(runner_id, lease_seconds, heartbeat)
            self._attempts[attempt_id] = updated
            return deepcopy(updated)

    def renew_attempt_lease(
        self,
        attempt_id: UUID,
        runner_id: str,
        lease_seconds: int,
        now: datetime | None = None,
    ) -> AttemptRecord:
        heartbeat = now or datetime.now(timezone.utc)
        with self._lock:
            current = self._get_attempt_locked(attempt_id)
            updated = current.with_heartbeat(runner_id, lease_seconds, heartbeat)
            self._attempts[attempt_id] = updated
            return deepcopy(updated)

    def expire_attempt_leases(
        self, now: datetime | None = None
    ) -> list[AttemptRecord]:
        cutoff = now or datetime.now(timezone.utc)
        expired: list[AttemptRecord] = []
        with self._lock:
            for attempt_id, current in list(self._attempts.items()):
                if (
                    current.state not in ATTEMPT_LEASED
                    or current.lease_expires_at is None
                    or current.lease_expires_at > cutoff
                ):
                    continue
                updated = current.lost(cutoff)
                self._attempts[attempt_id] = updated
                run = self._runs[current.run_id]
                if run.state not in RUN_TERMINAL:
                    self._runs[run.id] = run.with_state(RunState.INFRA_ERROR)
                expired.append(updated)
        return deepcopy(expired)

    def complete_attempt(
        self, attempt_id: UUID, target: AttemptState, result: ResultEnvelope
    ) -> AttemptRecord:
        with self._lock:
            current = self._get_attempt_locked(attempt_id)
            updated = current.with_result(target, result)
            self._attempts[attempt_id] = updated
            return deepcopy(updated)

    def append_event(self, event: EventEnvelope) -> EventRecord:
        with self._lock:
            attempt = self._get_attempt_locked(event.attempt_id)
            if attempt.run_id != event.run_id:
                raise EventConflict("event run_id does not match its attempt")
            run = self._get_locked(event.run_id)
            if run.engine is not event.engine:
                raise EventConflict("event engine does not match its run")
            existing = self._events.get(event.event_id)
            if existing is not None:
                if self._event_matches(existing, event):
                    return deepcopy(existing)
                raise EventConflict(f"event {event.event_id} has conflicting contents")
            sequence_key = (event.attempt_id, event.seq)
            if sequence_key in self._event_sequences:
                raise EventConflict(
                    f"attempt {event.attempt_id} already has event sequence {event.seq}"
                )
            record = EventRecord(
                cursor=self._next_cursor,
                event_id=event.event_id,
                run_id=event.run_id,
                attempt_id=event.attempt_id,
                engine=event.engine,
                seq=event.seq,
                occurred_at=event.occurred_at,
                type=event.type,
                payload=event.model_dump(mode="json")["payload"],
            )
            self._next_cursor += 1
            self._events[event.event_id] = record
            self._event_sequences[sequence_key] = event.event_id
            return deepcopy(record)

    def list_events(self, run_id: UUID, after: int = 0) -> list[EventRecord]:
        if after < 0:
            raise ValueError("event cursor must not be negative")
        with self._lock:
            self._get_locked(run_id)
            events = [
                item
                for item in self._events.values()
                if item.run_id == run_id and item.cursor > after
            ]
            return deepcopy(sorted(events, key=lambda item: item.cursor))

    def create_artifact(
        self,
        artifact_id: UUID,
        run_id: UUID,
        attempt_id: UUID,
        kind: str,
        name: str,
        content_type: str,
        size_bytes: int,
        storage_key: str,
        created_at: datetime,
    ) -> ArtifactRecord:
        record = ArtifactRecord(
            artifact_id, run_id, attempt_id, kind, name, content_type,
            size_bytes, storage_key, created_at,
        )
        with self._lock:
            attempt = self._get_attempt_locked(attempt_id)
            if attempt.run_id != run_id:
                raise ArtifactConflict("artifact run_id does not match its attempt")
            self._get_locked(run_id)
            existing = self._artifacts.get(artifact_id)
            if existing is not None:
                if existing == record:
                    return deepcopy(existing)
                raise ArtifactConflict(f"artifact {artifact_id} has conflicting contents")
            self._artifacts[artifact_id] = record
            return deepcopy(record)

    def get_artifact(self, artifact_id: UUID) -> ArtifactRecord:
        with self._lock:
            try:
                return deepcopy(self._artifacts[artifact_id])
            except KeyError as exc:
                raise ArtifactNotFound(f"artifact {artifact_id} was not found") from exc

    def list_artifacts(self, run_id: UUID) -> list[ArtifactRecord]:
        with self._lock:
            self._get_locked(run_id)
            records = [item for item in self._artifacts.values() if item.run_id == run_id]
            return deepcopy(sorted(records, key=lambda item: (item.created_at, item.id)))

    def _get_locked(self, run_id: UUID) -> RunRecord:
        try:
            return self._runs[run_id]
        except KeyError as exc:
            raise RunNotFound(f"run {run_id} was not found") from exc

    def _get_attempt_locked(self, attempt_id: UUID) -> AttemptRecord:
        try:
            return self._attempts[attempt_id]
        except KeyError as exc:
            raise AttemptNotFound(f"attempt {attempt_id} was not found") from exc

    @staticmethod
    def _event_matches(record: EventRecord, event: EventEnvelope) -> bool:
        payload = event.model_dump(mode="json")["payload"]
        return (
            record.run_id == event.run_id
            and record.attempt_id == event.attempt_id
            and record.engine is event.engine
            and record.seq == event.seq
            and record.occurred_at == event.occurred_at
            and record.type is event.type
            and record.payload == payload
        )


def __getattr__(name: str) -> type:
    if name == "SqlRunRepository":
        from .sql_repository import SqlRunRepository

        return SqlRunRepository
    raise AttributeError(name)