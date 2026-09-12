from collections.abc import Iterator
from datetime import datetime, timezone
from unittest.mock import MagicMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy import Engine
from sqlalchemy.dialects import mysql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from packages.protocol import (
    Engine as RunEngine,
    EventEnvelope,
    EventType,
    ResultEnvelope,
    ResultOutcome,
)
from services.api.database import Base, create_database_engine, create_session_factory
from services.api.domain import AttemptState, InvalidTransition, RunState
from services.api.models import RunModel
from services.api.repository import IdempotencyConflict, TerminalRunConflict
from services.api.sql_repository import SqlRunRepository


@pytest.fixture
def database(tmp_path) -> Iterator[tuple[Engine, sessionmaker]]:
    path = tmp_path / "runs.sqlite3"
    engine = create_database_engine(f"sqlite+pysqlite:///{path.as_posix()}")
    Base.metadata.create_all(engine)
    yield engine, create_session_factory(engine)
    engine.dispose()


def test_persists_create_list_get_and_utc_times(database) -> None:
    _, sessions = database
    first_repository = SqlRunRepository(sessions)
    created = first_repository.create(RunEngine.PYTEST, {"suite": "smoke"})

    second_repository = SqlRunRepository(sessions)
    loaded = second_repository.get(created.id)

    assert loaded == created
    assert loaded.created_at.tzinfo is timezone.utc
    assert loaded.updated_at.tzinfo is timezone.utc
    assert second_repository.list() == [created]


def test_idempotency_replay_and_conflicting_payload(database) -> None:
    _, sessions = database
    repository = SqlRunRepository(sessions)
    created, replayed = repository.create_or_get(
        RunEngine.HTTP, {"case": 1}, " same-key "
    )
    repeated, was_replayed = repository.create_or_get(
        RunEngine.HTTP, {"case": 1}, "same-key"
    )

    assert replayed is False
    assert was_replayed is True
    assert repeated.id == created.id
    assert len(repository.list()) == 1
    with pytest.raises(IdempotencyConflict):
        repository.create(RunEngine.HTTP, {"case": 2}, "same-key")


def test_idempotent_insert_race_reloads_in_a_new_transaction_without_locks() -> None:
    engine = RunEngine.HTTP
    parameters = {"case": 1}
    fingerprint = SqlRunRepository._fingerprint(engine, parameters)
    now = datetime.now(timezone.utc)
    winner_id = uuid4()
    winner = RunModel(
        id=str(winner_id),
        engine=engine.value,
        parameters=parameters,
        idempotency_key="same-key",
        fingerprint=fingerprint,
        state=RunState.CREATED.value,
        created_at=now,
        updated_at=now,
        state_version=0,
    )
    session = MagicMock()
    session.scalar.side_effect = [None, winner]
    session.flush.side_effect = IntegrityError("insert", {}, Exception("duplicate"))

    loaded, replayed = SqlRunRepository(lambda: session).create_or_get(
        engine, parameters, "same-key"
    )

    assert replayed is True
    assert loaded.id == winner_id
    assert session.begin.call_count == 2
    statements = [call.args[0] for call in session.scalar.call_args_list]
    assert all(
        "FOR UPDATE" not in str(statement.compile(dialect=mysql.dialect())).upper()
        for statement in statements
    )


def test_state_transition_lookup_keeps_row_lock() -> None:
    session = Mock()
    model = Mock()
    session.scalar.return_value = model

    assert SqlRunRepository._get_for_update(session, uuid4()) is model
    statement = session.scalar.call_args.args[0]
    assert "FOR UPDATE" in str(statement.compile(dialect=mysql.dialect())).upper()


def test_transition_cancel_and_terminal_guards(database) -> None:
    _, sessions = database
    repository = SqlRunRepository(sessions)
    created = repository.create(RunEngine.PLAYWRIGHT, {})

    cancelling = repository.cancel(created.id)
    assert cancelling.state is RunState.CANCELLING
    assert cancelling.state_version == 1
    assert repository.cancel(created.id).state_version == 1
    cancelled = repository.transition(created.id, RunState.CANCELLED)
    assert cancelled.state_version == 2

    with pytest.raises(TerminalRunConflict):
        repository.cancel(created.id)
    with pytest.raises(InvalidTransition):
        repository.transition(created.id, RunState.RUNNING)


def test_persists_attempts_and_idempotent_cursor_events(database) -> None:
    _, sessions = database
    repository = SqlRunRepository(sessions)
    run = repository.create(RunEngine.HTTP, {})
    attempt_id = uuid4()
    attempt = repository.create_attempt(run.id, attempt_id)
    event = EventEnvelope(
        run_id=run.id,
        attempt_id=attempt.id,
        engine=run.engine,
        event_id=uuid4(),
        seq=0,
        type=EventType.STARTED,
    )

    persisted = repository.append_event(event)

    assert repository.get_attempt(attempt_id) == attempt
    assert repository.list_attempts(run.id) == [attempt]
    assert repository.append_event(event) == persisted
    assert repository.list_events(run.id) == [persisted]
    assert repository.list_events(run.id, persisted.cursor) == []

    for state in (
        AttemptState.CLAIMED,
        AttemptState.PREPARING,
        AttemptState.RUNNING,
        AttemptState.COLLECTING,
    ):
        repository.transition_attempt(attempt.id, state)
    result = ResultEnvelope(
        run_id=run.id,
        attempt_id=attempt.id,
        engine=run.engine,
        outcome=ResultOutcome.SUCCEEDED,
        duration_ms=12,
        summary={"passed": 1},
    )
    completed = repository.complete_attempt(
        attempt.id, AttemptState.SUCCEEDED, result
    )
    assert completed.outcome is ResultOutcome.SUCCEEDED
    assert completed.duration_ms == 12
    assert completed.summary == {"passed": 1}
    assert repository.get_attempt(attempt.id) == completed