from collections.abc import Callable
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from packages.protocol import Engine, EventEnvelope, EventType, ResultEnvelope, ResultOutcome

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
from .models import ArtifactModel, AttemptModel, EventModel, RunModel
from .repository import (
    AttemptConflict,
    AttemptNotFound,
    ArtifactConflict,
    ArtifactNotFound,
    EventConflict,
    IdempotencyConflict,
    RunNotFound,
    RunRepository,
    TerminalRunConflict,
)

SessionFactory = Callable[[], Session]


class SqlRunRepository(RunRepository):
    """Transactional Run repository backed by SQLAlchemy."""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

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
        session = self._session_factory()
        try:
            try:
                with session.begin():
                    if key:
                        # Idempotency fields are immutable. A plain lookup avoids a
                        # MySQL gap lock when the key does not exist; the unique
                        # constraint below arbitrates concurrent inserts.
                        existing = self._get_by_key(session, key)
                        if existing is not None:
                            return self._resolve_idempotency(existing, fingerprint)

                    now = datetime.now(timezone.utc)
                    model = RunModel(
                        id=str(uuid4()),
                        engine=engine.value,
                        parameters=deepcopy(parameters),
                        idempotency_key=key,
                        fingerprint=fingerprint,
                        state=RunState.CREATED.value,
                        created_at=now,
                        updated_at=now,
                        state_version=0,
                    )
                    session.add(model)
                    session.flush()
                    return self._to_record(model), False
            except IntegrityError as conflict:
                if not key:
                    raise
                # The failed insert transaction has been rolled back by begin().
                # Read the winning row in a fresh transaction/snapshot.
                with session.begin():
                    existing = self._get_by_key(session, key)
                    if existing is None:
                        raise conflict
                    return self._resolve_idempotency(existing, fingerprint)
        finally:
            session.close()

    def list(self) -> list[RunRecord]:
        with self._session_factory() as session:
            models = session.scalars(
                select(RunModel).order_by(RunModel.created_at, RunModel.id)
            ).all()
            return [self._to_record(model) for model in models]

    def get(self, run_id: UUID) -> RunRecord:
        with self._session_factory() as session:
            model = session.get(RunModel, str(run_id))
            if model is None:
                raise RunNotFound(f"run {run_id} was not found")
            return self._to_record(model)

    def transition(self, run_id: UUID, target: RunState) -> RunRecord:
        with self._session_factory() as session, session.begin():
            model = self._get_for_update(session, run_id)
            updated = self._to_record(model).with_state(target)
            self._apply_state(model, updated)
            session.flush()
            return updated

    def cancel(self, run_id: UUID) -> RunRecord:
        with self._session_factory() as session, session.begin():
            model = self._get_for_update(session, run_id)
            current = self._to_record(model)
            if current.state in RUN_TERMINAL:
                raise TerminalRunConflict(f"run is already {current.state.value}")
            if current.state is RunState.CANCELLING:
                return current
            updated = current.with_state(RunState.CANCELLING)
            self._apply_state(model, updated)
            session.flush()
            return updated

    def create_attempt(
        self, run_id: UUID, attempt_id: UUID | None = None
    ) -> AttemptRecord:
        resolved_id = attempt_id or uuid4()
        with self._session_factory() as session, session.begin():
            self._get_for_update(session, run_id)
            if session.get(AttemptModel, str(resolved_id)) is not None:
                raise AttemptConflict(f"attempt {resolved_id} already exists")
            now = datetime.now(timezone.utc)
            model = AttemptModel(
                id=str(resolved_id),
                run_id=str(run_id),
                state=AttemptState.PENDING.value,
                created_at=now,
                updated_at=now,
                state_version=0,
                outcome=None,
                duration_ms=None,
                summary=None,
                completed_at=None,
                runner_id=None,
                heartbeat_at=None,
                lease_expires_at=None,
            )
            session.add(model)
            try:
                session.flush()
            except IntegrityError as exc:
                raise AttemptConflict(f"attempt {resolved_id} already exists") from exc
            return self._attempt_to_record(model)

    def begin_attempt(
        self,
        run_id: UUID,
        attempt_id: UUID,
        runner_id: str,
        lease_seconds: int,
    ) -> tuple[RunRecord, AttemptRecord]:
        with self._session_factory() as session, session.begin():
            run_model = self._get_for_update(session, run_id)
            running = self._to_record(run_model).with_state(RunState.RUNNING)
            if session.get(AttemptModel, str(attempt_id)) is not None:
                raise AttemptConflict(f"attempt {attempt_id} already exists")
            now = datetime.now(timezone.utc)
            pending = AttemptRecord(
                attempt_id, run_id, AttemptState.PENDING, now, now
            )
            claimed = pending.claimed(runner_id, lease_seconds, now)
            model = AttemptModel(
                id=str(attempt_id),
                run_id=str(run_id),
                state=claimed.state.value,
                created_at=claimed.created_at,
                updated_at=claimed.updated_at,
                state_version=claimed.state_version,
                outcome=None,
                duration_ms=None,
                summary=None,
                completed_at=None,
                runner_id=claimed.runner_id,
                heartbeat_at=claimed.heartbeat_at,
                lease_expires_at=claimed.lease_expires_at,
            )
            self._apply_state(run_model, running)
            session.add(model)
            try:
                session.flush()
            except IntegrityError as exc:
                raise AttemptConflict(f"attempt {attempt_id} already exists") from exc
            return running, claimed

    def get_attempt(self, attempt_id: UUID) -> AttemptRecord:
        with self._session_factory() as session:
            model = session.get(AttemptModel, str(attempt_id))
            if model is None:
                raise AttemptNotFound(f"attempt {attempt_id} was not found")
            return self._attempt_to_record(model)

    def list_attempts(self, run_id: UUID) -> list[AttemptRecord]:
        with self._session_factory() as session:
            self._require_run(session, run_id)
            models = session.scalars(
                select(AttemptModel)
                .where(AttemptModel.run_id == str(run_id))
                .order_by(AttemptModel.created_at, AttemptModel.id)
            ).all()
            return [self._attempt_to_record(model) for model in models]

    def transition_attempt(
        self, attempt_id: UUID, target: AttemptState
    ) -> AttemptRecord:
        with self._session_factory() as session, session.begin():
            model = self._get_attempt_for_update(session, attempt_id)
            updated = self._attempt_to_record(model).with_state(target)
            model.state = updated.state.value
            model.updated_at = updated.updated_at
            model.state_version = updated.state_version
            session.flush()
            return updated

    def claim_attempt(
        self,
        attempt_id: UUID,
        runner_id: str,
        lease_seconds: int,
        now: datetime | None = None,
    ) -> AttemptRecord:
        heartbeat = now or datetime.now(timezone.utc)
        with self._session_factory() as session, session.begin():
            model = self._get_attempt_for_update(session, attempt_id)
            updated = self._attempt_to_record(model).claimed(
                runner_id, lease_seconds, heartbeat
            )
            self._apply_attempt(model, updated)
            session.flush()
            return updated

    def renew_attempt_lease(
        self,
        attempt_id: UUID,
        runner_id: str,
        lease_seconds: int,
        now: datetime | None = None,
    ) -> AttemptRecord:
        heartbeat = now or datetime.now(timezone.utc)
        with self._session_factory() as session, session.begin():
            model = self._get_attempt_for_update(session, attempt_id)
            updated = self._attempt_to_record(model).with_heartbeat(
                runner_id, lease_seconds, heartbeat
            )
            self._apply_attempt(model, updated)
            session.flush()
            return updated

    def expire_attempt_leases(
        self, now: datetime | None = None
    ) -> list[AttemptRecord]:
        cutoff = now or datetime.now(timezone.utc)
        expired: list[AttemptRecord] = []
        active_states = [state.value for state in ATTEMPT_LEASED]
        with self._session_factory() as session, session.begin():
            models = session.scalars(
                select(AttemptModel)
                .where(
                    AttemptModel.state.in_(active_states),
                    AttemptModel.lease_expires_at.is_not(None),
                    AttemptModel.lease_expires_at <= cutoff,
                )
                .with_for_update(skip_locked=True)
            ).all()
            for model in models:
                updated = self._attempt_to_record(model).lost(cutoff)
                self._apply_attempt(model, updated)
                run_model = self._get_for_update(session, updated.run_id)
                run = self._to_record(run_model)
                if run.state not in RUN_TERMINAL:
                    self._apply_state(run_model, run.with_state(RunState.INFRA_ERROR))
                expired.append(updated)
            session.flush()
        return expired

    def complete_attempt(
        self, attempt_id: UUID, target: AttemptState, result: ResultEnvelope
    ) -> AttemptRecord:
        with self._session_factory() as session, session.begin():
            model = self._get_attempt_for_update(session, attempt_id)
            updated = self._attempt_to_record(model).with_result(target, result)
            model.state = updated.state.value
            model.updated_at = updated.updated_at
            model.state_version = updated.state_version
            model.outcome = updated.outcome.value if updated.outcome else None
            model.duration_ms = updated.duration_ms
            model.summary = deepcopy(updated.summary)
            model.completed_at = updated.completed_at
            session.flush()
            return updated

    def append_event(self, event: EventEnvelope) -> EventRecord:
        session = self._session_factory()
        try:
            try:
                with session.begin():
                    existing = self._get_event_by_id(session, event.event_id)
                    if existing is not None:
                        return self._resolve_event(existing, event)
                    attempt = session.get(AttemptModel, str(event.attempt_id))
                    if attempt is None:
                        raise AttemptNotFound(
                            f"attempt {event.attempt_id} was not found"
                        )
                    if attempt.run_id != str(event.run_id):
                        raise EventConflict("event run_id does not match its attempt")
                    run = self._require_run(session, event.run_id)
                    if run.engine != event.engine.value:
                        raise EventConflict("event engine does not match its run")
                    model = EventModel(
                        event_id=str(event.event_id),
                        run_id=str(event.run_id),
                        attempt_id=str(event.attempt_id),
                        engine=event.engine.value,
                        seq=event.seq,
                        occurred_at=event.occurred_at,
                        type=event.type.value,
                        payload=event.model_dump(mode="json")["payload"],
                    )
                    session.add(model)
                    session.flush()
                    return self._event_to_record(model)
            except IntegrityError as conflict:
                with session.begin():
                    existing = self._get_event_by_id(session, event.event_id)
                    if existing is not None:
                        return self._resolve_event(existing, event)
                    raise EventConflict(
                        f"attempt {event.attempt_id} already has event sequence {event.seq}"
                    ) from conflict
        finally:
            session.close()

    def list_events(self, run_id: UUID, after: int = 0) -> list[EventRecord]:
        if after < 0:
            raise ValueError("event cursor must not be negative")
        with self._session_factory() as session:
            self._require_run(session, run_id)
            models = session.scalars(
                select(EventModel)
                .where(
                    EventModel.run_id == str(run_id), EventModel.cursor > after
                )
                .order_by(EventModel.cursor)
            ).all()
            return [self._event_to_record(model) for model in models]

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
        expected = ArtifactRecord(
            artifact_id, run_id, attempt_id, kind, name, content_type,
            size_bytes, storage_key, created_at,
        )
        session = self._session_factory()
        try:
            try:
                with session.begin():
                    existing = session.get(ArtifactModel, str(artifact_id))
                    if existing is not None:
                        return self._resolve_artifact(existing, expected)
                    attempt = self._get_attempt_for_update(session, attempt_id)
                    if attempt.run_id != str(run_id):
                        raise ArtifactConflict(
                            "artifact run_id does not match its attempt"
                        )
                    model = ArtifactModel(
                        id=str(artifact_id),
                        run_id=str(run_id),
                        attempt_id=str(attempt_id),
                        kind=kind,
                        name=name,
                        content_type=content_type,
                        size_bytes=size_bytes,
                        storage_key=storage_key,
                        created_at=created_at,
                    )
                    session.add(model)
                    session.flush()
                    return self._artifact_to_record(model)
            except IntegrityError as conflict:
                with session.begin():
                    existing = session.get(ArtifactModel, str(artifact_id))
                    if existing is None:
                        raise conflict
                    return self._resolve_artifact(existing, expected)
        finally:
            session.close()

    def get_artifact(self, artifact_id: UUID) -> ArtifactRecord:
        with self._session_factory() as session:
            model = session.get(ArtifactModel, str(artifact_id))
            if model is None:
                raise ArtifactNotFound(f"artifact {artifact_id} was not found")
            return self._artifact_to_record(model)

    def list_artifacts(self, run_id: UUID) -> list[ArtifactRecord]:
        with self._session_factory() as session:
            self._require_run(session, run_id)
            models = session.scalars(
                select(ArtifactModel)
                .where(ArtifactModel.run_id == str(run_id))
                .order_by(ArtifactModel.created_at, ArtifactModel.id)
            ).all()
            return [self._artifact_to_record(model) for model in models]

    @staticmethod
    def _get_by_key(session: Session, key: str) -> RunModel | None:
        return session.scalar(
            select(RunModel).where(RunModel.idempotency_key == key)
        )

    @staticmethod
    def _require_run(session: Session, run_id: UUID) -> RunModel:
        model = session.get(RunModel, str(run_id))
        if model is None:
            raise RunNotFound(f"run {run_id} was not found")
        return model

    @staticmethod
    def _get_event_by_id(session: Session, event_id: UUID) -> EventModel | None:
        return session.scalar(
            select(EventModel).where(EventModel.event_id == str(event_id))
        )

    @staticmethod
    def _resolve_event(model: EventModel, event: EventEnvelope) -> EventRecord:
        record = SqlRunRepository._event_to_record(model)
        if not RunRepository._event_matches(record, event):
            raise EventConflict(f"event {event.event_id} has conflicting contents")
        return record

    @staticmethod
    def _resolve_artifact(
        model: ArtifactModel, expected: ArtifactRecord
    ) -> ArtifactRecord:
        record = SqlRunRepository._artifact_to_record(model)
        if (
            record.id != expected.id
            or record.run_id != expected.run_id
            or record.attempt_id != expected.attempt_id
            or record.kind != expected.kind
            or record.name != expected.name
            or record.content_type != expected.content_type
            or record.size_bytes != expected.size_bytes
            or record.storage_key != expected.storage_key
        ):
            raise ArtifactConflict(f"artifact {expected.id} has conflicting contents")
        return record

    @staticmethod
    def _resolve_idempotency(
        model: RunModel, fingerprint: str
    ) -> tuple[RunRecord, bool]:
        if model.fingerprint != fingerprint:
            raise IdempotencyConflict("idempotency key was used for another request")
        return SqlRunRepository._to_record(model), True

    @staticmethod
    def _get_for_update(session: Session, run_id: UUID) -> RunModel:
        model = session.scalar(
            select(RunModel)
            .where(RunModel.id == str(run_id))
            .with_for_update()
        )
        if model is None:
            raise RunNotFound(f"run {run_id} was not found")
        return model

    @staticmethod
    def _get_attempt_for_update(session: Session, attempt_id: UUID) -> AttemptModel:
        model = session.scalar(
            select(AttemptModel)
            .where(AttemptModel.id == str(attempt_id))
            .with_for_update()
        )
        if model is None:
            raise AttemptNotFound(f"attempt {attempt_id} was not found")
        return model

    @staticmethod
    def _apply_state(model: RunModel, updated: RunRecord) -> None:
        model.state = updated.state.value
        model.updated_at = updated.updated_at
        model.state_version = updated.state_version

    @staticmethod
    def _apply_attempt(model: AttemptModel, updated: AttemptRecord) -> None:
        model.state = updated.state.value
        model.updated_at = updated.updated_at
        model.state_version = updated.state_version
        model.outcome = updated.outcome.value if updated.outcome else None
        model.duration_ms = updated.duration_ms
        model.summary = deepcopy(updated.summary)
        model.completed_at = updated.completed_at
        model.runner_id = updated.runner_id
        model.heartbeat_at = updated.heartbeat_at
        model.lease_expires_at = updated.lease_expires_at

    @staticmethod
    def _to_record(model: RunModel) -> RunRecord:
        return RunRecord(
            id=UUID(model.id),
            engine=Engine(model.engine),
            parameters=deepcopy(model.parameters),
            state=RunState(model.state),
            created_at=SqlRunRepository._as_utc(model.created_at),
            updated_at=SqlRunRepository._as_utc(model.updated_at),
            state_version=model.state_version,
        )

    @staticmethod
    def _attempt_to_record(model: AttemptModel) -> AttemptRecord:
        return AttemptRecord(
            id=UUID(model.id),
            run_id=UUID(model.run_id),
            state=AttemptState(model.state),
            created_at=SqlRunRepository._as_utc(model.created_at),
            updated_at=SqlRunRepository._as_utc(model.updated_at),
            state_version=model.state_version,
            outcome=ResultOutcome(model.outcome) if model.outcome else None,
            duration_ms=model.duration_ms,
            summary=deepcopy(model.summary),
            completed_at=(
                SqlRunRepository._as_utc(model.completed_at)
                if model.completed_at else None
            ),
            runner_id=model.runner_id,
            heartbeat_at=(
                SqlRunRepository._as_utc(model.heartbeat_at)
                if model.heartbeat_at else None
            ),
            lease_expires_at=(
                SqlRunRepository._as_utc(model.lease_expires_at)
                if model.lease_expires_at else None
            ),
        )

    @staticmethod
    def _event_to_record(model: EventModel) -> EventRecord:
        return EventRecord(
            cursor=model.cursor,
            event_id=UUID(model.event_id),
            run_id=UUID(model.run_id),
            attempt_id=UUID(model.attempt_id),
            engine=Engine(model.engine),
            seq=model.seq,
            occurred_at=SqlRunRepository._as_utc(model.occurred_at),
            type=EventType(model.type),
            payload=deepcopy(model.payload),
        )

    @staticmethod
    def _artifact_to_record(model: ArtifactModel) -> ArtifactRecord:
        return ArtifactRecord(
            id=UUID(model.id),
            run_id=UUID(model.run_id),
            attempt_id=UUID(model.attempt_id),
            kind=model.kind,
            name=model.name,
            content_type=model.content_type,
            size_bytes=model.size_bytes,
            storage_key=model.storage_key,
            created_at=SqlRunRepository._as_utc(model.created_at),
        )

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)