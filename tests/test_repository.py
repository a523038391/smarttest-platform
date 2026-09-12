from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

from packages.protocol import Engine, EventEnvelope, EventType
from services.api.domain import AttemptState, RunState
from services.api.repository import (
    EventConflict,
    IdempotencyConflict,
    RunRepository,
    TerminalRunConflict,
)


def test_idempotency_key_returns_one_run_under_concurrency() -> None:
    repository = RunRepository()

    def create() -> str:
        return str(repository.create(Engine.PYTEST, {"suite": "smoke"}, "same-key").id)

    with ThreadPoolExecutor(max_workers=8) as pool:
        ids = set(pool.map(lambda _: create(), range(24)))

    assert len(ids) == 1
    assert len(repository.list()) == 1


def test_idempotency_key_rejects_different_request() -> None:
    repository = RunRepository()
    repository.create(Engine.HTTP, {"case": 1}, "key")

    with pytest.raises(IdempotencyConflict):
        repository.create(Engine.HTTP, {"case": 2}, "key")


def test_cancel_rules_and_terminal_conflict() -> None:
    repository = RunRepository()
    created = repository.create(Engine.HTTP, {})
    cancelling_created = repository.cancel(created.id)
    assert cancelling_created.state is RunState.CANCELLING
    assert repository.cancel(created.id).state_version == cancelling_created.state_version
    repository.transition(created.id, RunState.CANCELLED)
    with pytest.raises(TerminalRunConflict):
        repository.cancel(created.id)

    queued = repository.create(Engine.PYTEST, {})
    repository.transition(queued.id, RunState.QUEUED)
    assert repository.cancel(queued.id).state is RunState.CANCELLING

    running = repository.create(Engine.PLAYWRIGHT, {})
    repository.transition(running.id, RunState.QUEUED)
    repository.transition(running.id, RunState.DISPATCHING)
    repository.transition(running.id, RunState.RUNNING)
    cancelling = repository.cancel(running.id)
    assert cancelling.state is RunState.CANCELLING
    assert repository.cancel(running.id).state_version == cancelling.state_version


def test_attempts_and_events_are_ordered_and_idempotent() -> None:
    repository = RunRepository()
    run = repository.create(Engine.PYTEST, {})
    attempt = repository.create_attempt(run.id)
    claimed = repository.transition_attempt(attempt.id, AttemptState.CLAIMED)
    event = EventEnvelope(
        run_id=run.id,
        attempt_id=attempt.id,
        engine=run.engine,
        event_id=uuid4(),
        seq=0,
        type=EventType.STARTED,
        payload={"case": 1},
    )

    first = repository.append_event(event)
    replay = repository.append_event(event)

    assert claimed.state_version == 1
    assert repository.list_attempts(run.id) == [claimed]
    assert first == replay
    assert repository.list_events(run.id, after=first.cursor) == []
    with pytest.raises(EventConflict):
        repository.append_event(event.model_copy(update={"event_id": uuid4()}))