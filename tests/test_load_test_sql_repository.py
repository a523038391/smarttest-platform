from datetime import datetime, timezone

import pytest

from services.api.database import Base, create_database_engine, create_session_factory
from services.api.environment_crypto import SecretEncryptor
from services.api.environment_sql_repository import SqlEnvironmentRepository
from services.api.load_test_domain import (
    LoadTestMode, LoadTestRunStatus, LoadTestTrafficMode,
)
from services.api.load_test_repository import LoadTestActiveRunConflict
from services.api.load_test_sql_repository import SqlLoadTestRepository
from services.api.project_sql_repository import SqlProjectRepository


TARGETS = [{
    "name": "API", "method": "GET", "url": "https://example.com",
    "headers": {}, "query": {}, "body": None, "expected_statuses": [200],
}]


def test_sql_definition_and_run_round_trip(tmp_path) -> None:
    engine = create_database_engine(
        f"sqlite+pysqlite:///{(tmp_path / 'load.sqlite3').as_posix()}"
    )
    Base.metadata.create_all(engine)
    sessions = create_session_factory(engine)
    project = SqlProjectRepository(sessions).create_project("Load SQL", "")
    environment = SqlEnvironmentRepository(
        sessions, SecretEncryptor(None, None)
    ).create_environment(project.id, "staging", [], [])
    repository = SqlLoadTestRepository(sessions)
    definition = repository.create_load_test(
        project.id, "Load", "", [{
            **TARGETS[0], "extractors": {"item_id": "data.items.0.id"},
            "think_time_ms": 25,
        }], LoadTestMode.COUNT, 2, 1, 1, 0, 5,
        environment_id=environment.id,
        traffic_mode=LoadTestTrafficMode.SCENARIO,
        initial_variables={"seed": [1, 2]}, stop_on_failure=False,
    )
    assert definition.environment_id == environment.id
    assert definition.traffic_mode == LoadTestTrafficMode.SCENARIO
    assert definition.initial_variables == {"seed": [1, 2]}
    assert definition.stop_on_failure is False
    assert definition.targets[0]["think_time_ms"] == 25
    assert repository.get_load_test(definition.id) == definition
    run = repository.create_run(definition.id)
    with pytest.raises(LoadTestActiveRunConflict):
        repository.create_run(definition.id)
    completed = repository.update_run(
        run.id, status=LoadTestRunStatus.SUCCEEDED,
        metrics={**run.metrics, "total_requests": 2},
        finished_at=datetime.now(timezone.utc),
    )
    assert repository.get_run(run.id) == completed
    assert repository.list_runs(definition.id) == [completed]
    engine.dispose()


def test_sql_recovery_releases_orphaned_run_guard(tmp_path) -> None:
    engine = create_database_engine(
        f"sqlite+pysqlite:///{(tmp_path / 'recovery.sqlite3').as_posix()}"
    )
    Base.metadata.create_all(engine)
    sessions = create_session_factory(engine)
    project = SqlProjectRepository(sessions).create_project("Recovery", "")
    repository = SqlLoadTestRepository(sessions)
    definition = repository.create_load_test(
        project.id, "Load", "", TARGETS, LoadTestMode.COUNT, 2, 1, 1, 0, 5,
    )
    orphaned = repository.create_run(definition.id)

    assert repository.recover_running_runs("service restarted") == 1
    assert repository.get_run(orphaned.id).status == LoadTestRunStatus.FAILED
    assert repository.create_run(definition.id).status == LoadTestRunStatus.RUNNING
    engine.dispose()