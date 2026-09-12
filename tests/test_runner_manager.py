from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from packages.protocol import (
    Engine,
    EventEnvelope,
    EventType,
    ResultEnvelope,
    ResultOutcome,
    TaskEnvelope,
)
from services.api.domain import AttemptState, RunState
from services.api.repository import RunRepository
from services.runner_manager import RunnerIdentityError, RunnerManager


def task_for(run_id, engine) -> TaskEnvelope:
    now = datetime.now(timezone.utc)
    return TaskEnvelope(
        run_id=run_id,
        attempt_id=uuid4(),
        task_id=uuid4(),
        tenant_id=uuid4(),
        project_id=uuid4(),
        idempotency_key="manager-test",
        engine=engine,
        created_at=now,
        deadline=now + timedelta(minutes=1),
        entrypoint="test_case.py",
    )


def dispatch(repository: RunRepository, run_id) -> None:
    repository.transition(run_id, RunState.QUEUED)
    repository.transition(run_id, RunState.DISPATCHING)


@pytest.mark.parametrize(
    ("outcome", "attempt_state", "run_state"),
    [
        (ResultOutcome.SUCCEEDED, AttemptState.SUCCEEDED, RunState.SUCCEEDED),
        (ResultOutcome.FAILED, AttemptState.FAILED, RunState.FAILED),
        (ResultOutcome.CANCELLED, AttemptState.CANCELLED, RunState.CANCELLED),
        (ResultOutcome.TIMED_OUT, AttemptState.TIMED_OUT, RunState.TIMED_OUT),
        (ResultOutcome.INFRA_ERROR, AttemptState.INFRA_ERROR, RunState.INFRA_ERROR),
    ],
)
def test_manager_persists_events_and_terminal_states(
    outcome, attempt_state, run_state
) -> None:
    repository = RunRepository()
    run = repository.create(Engine.PYTEST, {})
    dispatch(repository, run.id)
    task = task_for(run.id, run.engine)

    def execute(envelope, sink):
        sink(EventEnvelope(
            run_id=envelope.run_id,
            attempt_id=envelope.attempt_id,
            engine=envelope.engine,
            event_id=uuid4(),
            seq=0,
            type=EventType.FINISHED,
        ))
        return ResultEnvelope(
            run_id=envelope.run_id,
            attempt_id=envelope.attempt_id,
            engine=envelope.engine,
            outcome=outcome,
            duration_ms=1,
        )

    RunnerManager(repository, execute).execute(task)

    assert repository.get(task.run_id).state is run_state
    persisted_attempt = repository.get_attempt(task.attempt_id)
    assert persisted_attempt.state is attempt_state
    assert persisted_attempt.outcome is outcome
    assert persisted_attempt.duration_ms == 1
    assert persisted_attempt.completed_at is not None
    assert len(repository.list_events(task.run_id)) == 1


def test_manager_rejects_wrong_event_identity() -> None:
    repository = RunRepository()
    engine = Engine.HTTP
    run = repository.create(engine, {})
    dispatch(repository, run.id)
    task = task_for(run.id, engine)

    def execute(envelope, sink):
        sink(EventEnvelope(
            run_id=uuid4(),
            attempt_id=envelope.attempt_id,
            engine=envelope.engine,
            event_id=uuid4(),
            seq=0,
            type=EventType.STARTED,
        ))

    with pytest.raises(RunnerIdentityError):
        RunnerManager(repository, execute).execute(task)
    assert repository.list_events(run.id) == []
    assert repository.get(run.id).state is RunState.INFRA_ERROR
    assert repository.get_attempt(task.attempt_id).state is AttemptState.INFRA_ERROR


def test_cancellation_wins_when_executor_returns_success() -> None:
    repository = RunRepository()
    run = repository.create(Engine.HTTP, {})
    dispatch(repository, run.id)
    task = task_for(run.id, run.engine)

    def execute(envelope, _sink):
        repository.cancel(envelope.run_id)
        return ResultEnvelope(
            run_id=envelope.run_id,
            attempt_id=envelope.attempt_id,
            engine=envelope.engine,
            outcome=ResultOutcome.SUCCEEDED,
            duration_ms=3,
        )

    result = RunnerManager(repository, execute).execute(task)

    assert result.outcome is ResultOutcome.CANCELLED
    assert result.summary["cancel_requested"] is True
    assert repository.get(run.id).state is RunState.CANCELLED
    assert repository.get_attempt(task.attempt_id).state is AttemptState.CANCELLED


def test_second_manager_cannot_claim_an_already_running_run() -> None:
    repository = RunRepository()
    run = repository.create(Engine.HTTP, {})
    dispatch(repository, run.id)
    first_task = task_for(run.id, run.engine)
    second_task = task_for(run.id, run.engine)

    def execute(envelope, _sink):
        with pytest.raises(RunnerIdentityError, match="cannot execute"):
            RunnerManager(repository, lambda *_: None).execute(second_task)
        return ResultEnvelope(
            run_id=envelope.run_id,
            attempt_id=envelope.attempt_id,
            engine=envelope.engine,
            outcome=ResultOutcome.SUCCEEDED,
            duration_ms=1,
        )

    RunnerManager(repository, execute).execute(first_task)
    assert [item.id for item in repository.list_attempts(run.id)] == [
        first_task.attempt_id
    ]