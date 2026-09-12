from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, TypeVar
from uuid import UUID

from packages.protocol import Engine, EventType, ResultEnvelope, ResultOutcome


class RunState(str, Enum):
    CREATED = "CREATED"
    QUEUED = "QUEUED"
    DISPATCHING = "DISPATCHING"
    RUNNING = "RUNNING"
    RETRY_WAIT = "RETRY_WAIT"
    CANCELLING = "CANCELLING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    TIMED_OUT = "TIMED_OUT"
    INFRA_ERROR = "INFRA_ERROR"


class AttemptState(str, Enum):
    PENDING = "PENDING"
    CLAIMED = "CLAIMED"
    PREPARING = "PREPARING"
    RUNNING = "RUNNING"
    COLLECTING = "COLLECTING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    TIMED_OUT = "TIMED_OUT"
    INFRA_ERROR = "INFRA_ERROR"
    LOST = "LOST"


RUN_TERMINAL = frozenset(
    {RunState.SUCCEEDED, RunState.FAILED, RunState.CANCELLED,
     RunState.TIMED_OUT, RunState.INFRA_ERROR}
)
ATTEMPT_TERMINAL = frozenset(
    {AttemptState.SUCCEEDED, AttemptState.FAILED, AttemptState.CANCELLED,
     AttemptState.TIMED_OUT, AttemptState.INFRA_ERROR, AttemptState.LOST}
)
ATTEMPT_LEASED = frozenset(
    {
        AttemptState.CLAIMED,
        AttemptState.PREPARING,
        AttemptState.RUNNING,
        AttemptState.COLLECTING,
    }
)

RUN_TRANSITIONS = {
    RunState.CREATED: {RunState.QUEUED, RunState.CANCELLING},
    RunState.QUEUED: {RunState.DISPATCHING, RunState.CANCELLING,
                      RunState.FAILED, RunState.TIMED_OUT, RunState.INFRA_ERROR},
    RunState.DISPATCHING: {RunState.RUNNING, RunState.CANCELLING,
                           RunState.FAILED, RunState.TIMED_OUT, RunState.INFRA_ERROR},
    RunState.RUNNING: {RunState.CANCELLING, RunState.SUCCEEDED, RunState.FAILED,
                       RunState.TIMED_OUT, RunState.INFRA_ERROR, RunState.RETRY_WAIT},
    RunState.RETRY_WAIT: {RunState.QUEUED, RunState.CANCELLING, RunState.INFRA_ERROR},
    RunState.CANCELLING: {RunState.CANCELLED, RunState.FAILED,
                          RunState.TIMED_OUT, RunState.INFRA_ERROR},
}
ATTEMPT_TRANSITIONS = {
    AttemptState.PENDING: {AttemptState.CLAIMED, AttemptState.CANCELLED},
    AttemptState.CLAIMED: {AttemptState.PREPARING, AttemptState.CANCELLED,
                           AttemptState.INFRA_ERROR, AttemptState.LOST},
    AttemptState.PREPARING: {AttemptState.RUNNING, AttemptState.CANCELLED,
                             AttemptState.INFRA_ERROR, AttemptState.LOST},
    AttemptState.RUNNING: {AttemptState.COLLECTING, AttemptState.CANCELLED,
                           AttemptState.FAILED, AttemptState.TIMED_OUT,
                           AttemptState.INFRA_ERROR, AttemptState.LOST},
    AttemptState.COLLECTING: set(ATTEMPT_TERMINAL),
}


class InvalidTransition(ValueError):
    pass


State = TypeVar("State", RunState, AttemptState)


def _transition(current: State, target: State, rules: dict) -> State:
    if target not in rules.get(current, set()):
        raise InvalidTransition(f"cannot transition from {current.value} to {target.value}")
    return target


def transition_run(current: RunState, target: RunState) -> RunState:
    return _transition(current, target, RUN_TRANSITIONS)


def transition_attempt(current: AttemptState, target: AttemptState) -> AttemptState:
    return _transition(current, target, ATTEMPT_TRANSITIONS)


@dataclass(frozen=True, slots=True)
class RunRecord:
    id: UUID
    engine: Engine
    parameters: dict[str, Any]
    state: RunState
    created_at: datetime
    updated_at: datetime
    state_version: int = 0

    def with_state(self, state: RunState) -> "RunRecord":
        next_state = transition_run(self.state, state)
        return replace(self, state=next_state, updated_at=datetime.now(timezone.utc),
                       state_version=self.state_version + 1)


@dataclass(frozen=True, slots=True)
class AttemptRecord:
    id: UUID
    run_id: UUID
    state: AttemptState
    created_at: datetime
    updated_at: datetime
    state_version: int = 0
    outcome: ResultOutcome | None = None
    duration_ms: int | None = None
    summary: dict[str, Any] | None = None
    completed_at: datetime | None = None
    runner_id: str | None = None
    heartbeat_at: datetime | None = None
    lease_expires_at: datetime | None = None

    def with_state(self, state: AttemptState) -> "AttemptRecord":
        next_state = transition_attempt(self.state, state)
        return replace(
            self,
            state=next_state,
            updated_at=datetime.now(timezone.utc),
            state_version=self.state_version + 1,
        )

    def with_result(
        self, state: AttemptState, result: ResultEnvelope
    ) -> "AttemptRecord":
        if result.attempt_id != self.id or result.run_id != self.run_id:
            raise ValueError("result identity does not match the attempt")
        next_state = transition_attempt(self.state, state)
        return replace(
            self,
            state=next_state,
            updated_at=datetime.now(timezone.utc),
            state_version=self.state_version + 1,
            outcome=result.outcome,
            duration_ms=result.duration_ms,
            summary=result.model_dump(mode="json")["summary"],
            completed_at=result.completed_at,
        )

    def claimed(
        self, runner_id: str, lease_seconds: int, now: datetime
    ) -> "AttemptRecord":
        identity = runner_id.strip()
        if not identity or len(identity) > 255:
            raise ValueError("runner_id must contain 1 to 255 characters")
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        claimed = self.with_state(AttemptState.CLAIMED)
        return replace(
            claimed,
            runner_id=identity,
            heartbeat_at=now,
            lease_expires_at=now + timedelta(seconds=lease_seconds),
        )

    def with_heartbeat(
        self, runner_id: str, lease_seconds: int, now: datetime
    ) -> "AttemptRecord":
        if self.state not in ATTEMPT_LEASED:
            raise InvalidTransition(f"attempt is already {self.state.value}")
        if self.runner_id != runner_id:
            raise ValueError("runner identity does not own this attempt")
        if self.lease_expires_at is None or self.lease_expires_at <= now:
            raise InvalidTransition("attempt lease has expired")
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        return replace(
            self,
            updated_at=now,
            heartbeat_at=now,
            lease_expires_at=now + timedelta(seconds=lease_seconds),
        )

    def lost(self, now: datetime) -> "AttemptRecord":
        lost = self.with_state(AttemptState.LOST)
        return replace(
            lost,
            outcome=ResultOutcome.INFRA_ERROR,
            summary={"reason": "runner_lease_expired"},
            completed_at=now,
        )


@dataclass(frozen=True, slots=True)
class EventRecord:
    cursor: int
    event_id: UUID
    run_id: UUID
    attempt_id: UUID
    engine: Engine
    seq: int
    occurred_at: datetime
    type: EventType
    payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ArtifactRecord:
    id: UUID
    run_id: UUID
    attempt_id: UUID
    kind: str
    name: str
    content_type: str
    size_bytes: int
    storage_key: str
    created_at: datetime