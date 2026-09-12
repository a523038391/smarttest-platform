from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import sleep
from uuid import uuid4

import pytest

from packages.protocol import Engine, ResultOutcome
from runner import AdapterResult, RunnerAgent
from services.api.database import Base, create_database_engine, create_session_factory
from services.api.domain import AttemptState, RunState
from services.api.repository import RunRepository
from services.api.sql_repository import SqlRunRepository
from tests.test_runner import task
from workers.tasks import recover_lost_attempts


def _running_attempt(repository: RunRepository):
    run = repository.create(Engine.PYTEST, {})
    repository.transition(run.id, RunState.QUEUED)
    repository.transition(run.id, RunState.DISPATCHING)
    repository.transition(run.id, RunState.RUNNING)
    attempt = repository.create_attempt(run.id)
    claimed_at = datetime.now(timezone.utc)
    repository.claim_attempt(attempt.id, "runner-a", 10, claimed_at)
    repository.transition_attempt(attempt.id, AttemptState.PREPARING)
    repository.transition_attempt(attempt.id, AttemptState.RUNNING)
    return run, attempt, claimed_at


def test_heartbeat_renews_owned_attempt_and_rejects_stale_owner() -> None:
    repository = RunRepository()
    _, attempt, claimed_at = _running_attempt(repository)

    renewed = repository.renew_attempt_lease(
        attempt.id, "runner-a", 10, claimed_at + timedelta(seconds=5)
    )

    assert renewed.heartbeat_at == claimed_at + timedelta(seconds=5)
    assert renewed.lease_expires_at == claimed_at + timedelta(seconds=15)
    with pytest.raises(ValueError, match="does not own"):
        repository.renew_attempt_lease(attempt.id, "runner-b", 10)


def test_reaper_marks_expired_attempt_lost_and_is_idempotent() -> None:
    repository = RunRepository()
    run, attempt, claimed_at = _running_attempt(repository)

    assert recover_lost_attempts(repository, claimed_at + timedelta(seconds=11)) == 1
    lost = repository.get_attempt(attempt.id)
    assert lost.state is AttemptState.LOST
    assert lost.outcome is ResultOutcome.INFRA_ERROR
    assert lost.summary == {"reason": "runner_lease_expired"}
    assert repository.get(run.id).state is RunState.INFRA_ERROR
    assert recover_lost_attempts(repository, claimed_at + timedelta(seconds=12)) == 0


def test_reaper_recovers_attempt_stuck_collecting() -> None:
    repository = RunRepository()
    run, attempt, claimed_at = _running_attempt(repository)
    repository.transition_attempt(attempt.id, AttemptState.COLLECTING)

    assert recover_lost_attempts(repository, claimed_at + timedelta(seconds=11)) == 1
    assert repository.get_attempt(attempt.id).state is AttemptState.LOST
    assert repository.get(run.id).state is RunState.INFRA_ERROR


def test_sql_repository_persists_and_expires_lease(tmp_path: Path) -> None:
    engine = create_database_engine(
        f"sqlite+pysqlite:///{(tmp_path / 'lease.sqlite3').as_posix()}"
    )
    Base.metadata.create_all(engine)
    try:
        repository = SqlRunRepository(create_session_factory(engine))
        run, attempt, claimed_at = _running_attempt(repository)

        expired = repository.expire_attempt_leases(
            claimed_at + timedelta(seconds=11)
        )

        assert [item.id for item in expired] == [attempt.id]
        assert repository.get_attempt(attempt.id).runner_id == "runner-a"
        assert repository.get(run.id).state is RunState.INFRA_ERROR
    finally:
        engine.dispose()


def test_runner_agent_emits_heartbeats_without_breaking_sequence(tmp_path: Path) -> None:
    class SlowAdapter:
        def run(self, _task, _workspace):
            sleep(0.03)
            return AdapterResult(ResultOutcome.SUCCEEDED, "ok")

    events = []
    envelope = task()
    result = RunnerAgent(
        envelope,
        tmp_path,
        events.append,
        {Engine.PYTEST: SlowAdapter()},
        heartbeat_interval=0.005,
    ).run()

    assert result.outcome is ResultOutcome.SUCCEEDED
    assert any(event.type.value == "heartbeat" for event in events)
    assert [event.seq for event in events] == list(range(len(events)))