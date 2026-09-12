from inspect import signature
from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from packages.protocol import Engine
from services.api.app import create_app
from services.api.config import DEFAULT_REDIS_URL, Settings
from services.api.dispatcher import CeleryRunDispatcher, DatabaseRunDispatcher
from services.api.domain import InvalidTransition, RunState
from services.api.repository import RunRepository
from services.api.sql_repository import SqlRunRepository
from workers.celery_app import celery_app, create_celery_app
import workers.tasks as worker_tasks
from workers.tasks import (
    prepare_run,
    prepare_run_task,
    recover_lost_attempts_task,
)


@pytest.fixture(autouse=True)
def reset_worker_database_resources():
    worker_tasks.shutdown_worker_process()
    yield
    worker_tasks.shutdown_worker_process()


class RecordingDispatcher:
    def __init__(self, error: Exception | None = None) -> None:
        self.run_ids: list[object] = []
        self.error = error

    def dispatch(self, run_id: object) -> None:
        self.run_ids.append(run_id)
        if self.error is not None:
            raise self.error


def test_settings_from_env_defaults_booleans_and_redacted_repr() -> None:
    defaults = Settings.from_env({})
    assert defaults.database_url is None
    assert defaults.celery_broker_url == DEFAULT_REDIS_URL
    assert defaults.result_backend == DEFAULT_REDIS_URL
    assert defaults.auto_dispatch is False
    assert defaults.secret_capability_ttl_seconds == 60
    assert defaults.secret_staging_root is None

    settings = Settings.from_env(
        {
            "DATABASE_URL": "mysql+pymysql://user:credential-marker@db/runs",
            "CELERY_BROKER_URL": "redis://:credential-marker@redis/0",
            "RESULT_BACKEND": "redis://:credential-marker@redis/1",
            "CELERY_TASK_ALWAYS_EAGER": "yes",
            "AUTO_DISPATCH": "1",
            "ATTEMPT_REAPER_INTERVAL_SECONDS": "15",
            "SECRET_STAGING_ROOT": "/run/smarttest-secrets",
            "SECRET_CAPABILITY_TTL_SECONDS": "45",
            "RUNNER_UID": "1234",
            "RUNNER_GID": "1235",
        }
    )
    assert settings.celery_task_always_eager is True
    assert settings.auto_dispatch is True
    assert settings.attempt_reaper_interval_seconds == 15
    assert settings.secret_capability_ttl_seconds == 45
    assert settings.runner_uid == 1234 and settings.runner_gid == 1235
    assert "credential-marker" not in repr(settings)


def test_create_app_selects_repository_and_disposes_sql_engine(monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert isinstance(create_app().state.run_repository, RunRepository)

    engine = Mock()
    sessions = Mock()
    monkeypatch.setattr("services.api.app.create_database_engine", lambda _: engine)
    monkeypatch.setattr("services.api.app.create_session_factory", lambda _: sessions)
    monkeypatch.setenv("DATABASE_URL", "sqlite+pysqlite:///runs.db")
    app = create_app()

    assert isinstance(app.state.run_repository, SqlRunRepository)
    with TestClient(app):
        engine.dispose.assert_not_called()
    engine.dispose.assert_called_once_with()


def test_auto_dispatches_first_creation_but_not_idempotency_replay() -> None:
    repository = RunRepository()
    dispatcher = RecordingDispatcher()
    app = create_app(
        repository=repository,
        dispatcher=dispatcher,
        settings=Settings(auto_dispatch=True, auth_required=False),
    )
    client = TestClient(app)
    headers = {"Idempotency-Key": "dispatch-once"}

    first = client.post("/api/v1/runs", json={"engine": "http"}, headers=headers)
    replay = client.post("/api/v1/runs", json={"engine": "http"}, headers=headers)

    assert first.status_code == 201
    assert first.json()["state"] == "QUEUED"
    assert replay.status_code == 200
    assert replay.json()["state"] == "QUEUED"
    assert len(dispatcher.run_ids) == 1


def test_auto_dispatcher_uses_resolved_celery_settings(monkeypatch) -> None:
    celery = Mock()
    factory = Mock(return_value=celery)
    monkeypatch.setattr("workers.celery_app.create_celery_app", factory)
    settings = Settings(
        celery_broker_url="memory://",
        result_backend="cache+memory://",
        auto_dispatch=True,
    )

    app = create_app(settings=settings)

    factory.assert_called_once_with(settings)
    app.state.run_dispatcher.dispatch(uuid4())
    celery.send_task.assert_called_once()


def test_celery_dispatcher_sends_only_the_run_id() -> None:
    celery = Mock()
    run_id = uuid4()

    CeleryRunDispatcher(celery).dispatch(run_id)

    celery.send_task.assert_called_once_with(
        "smarttest.prepare_run", args=[str(run_id)]
    )


def test_database_dispatcher_advances_queued_run_idempotently() -> None:
    repository = RunRepository()
    run = repository.create(Engine.PYTEST, {})
    dispatcher = DatabaseRunDispatcher(repository)

    with pytest.raises(InvalidTransition, match="has not been queued"):
        dispatcher.dispatch(run.id)
    repository.transition(run.id, RunState.QUEUED)
    dispatcher.dispatch(run.id)
    dispatcher.dispatch(run.id)
    assert repository.get(run.id).state is RunState.DISPATCHING

    repository.transition(run.id, RunState.RUNNING)
    dispatcher.dispatch(run.id)
    repository.transition(run.id, RunState.SUCCEEDED)
    dispatcher.dispatch(run.id)
    assert repository.get(run.id).state is RunState.SUCCEEDED


def test_local_auto_dispatch_selects_database_dispatcher(tmp_path) -> None:
    settings = Settings(
        auto_dispatch=True,
        local_runner_enabled=True,
        auth_required=False,
        source_root=str(tmp_path / "sources"),
        runner_work_root=str(tmp_path / "work"),
        artifact_root=str(tmp_path / "artifacts"),
    )

    app = create_app(settings=settings)

    assert isinstance(app.state.run_dispatcher, DatabaseRunDispatcher)
    assert app.state.local_runner_service is not None


def test_dispatch_failure_returns_503_and_converges_to_infra_error() -> None:
    repository = RunRepository()
    dispatcher = RecordingDispatcher(RuntimeError("broker URL must not leak"))
    app = create_app(
        repository=repository,
        dispatcher=dispatcher,
        settings=Settings(auto_dispatch=True, auth_required=False),
    )

    response = TestClient(app).post("/api/v1/runs", json={"engine": "pytest"})

    assert response.status_code == 503
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["code"] == "dispatch_unavailable"
    assert "broker URL" not in response.text
    assert repository.list()[0].state is RunState.INFRA_ERROR


def test_prepare_run_is_idempotent_and_protects_advanced_states() -> None:
    repository = RunRepository()
    run = repository.create(Engine.PYTEST, {})
    repository.transition(run.id, RunState.QUEUED)

    dispatching = prepare_run(repository, run.id)
    assert dispatching.state is RunState.DISPATCHING
    assert prepare_run(repository, run.id) == dispatching

    running = repository.transition(run.id, RunState.RUNNING)
    assert prepare_run(repository, run.id) == running
    succeeded = repository.transition(run.id, RunState.SUCCEEDED)
    assert prepare_run(repository, run.id) == succeeded


def test_prepare_run_rejects_created_state() -> None:
    repository = RunRepository()
    run = repository.create(Engine.HTTP, {})

    with pytest.raises(InvalidTransition, match="has not been queued"):
        prepare_run(repository, run.id)


def test_prepare_run_task_reuses_process_engine_and_repository(monkeypatch) -> None:
    engine = Mock()
    repository = Mock()
    run = Mock(id=uuid4(), state=RunState.DISPATCHING)
    engine_factory = Mock(return_value=engine)
    session_factory = Mock()
    repository_factory = Mock(return_value=repository)
    monkeypatch.setattr(
        worker_tasks.Settings,
        "from_env",
        classmethod(lambda _: Settings(database_url="sqlite+pysqlite:///runs.db")),
    )
    monkeypatch.setattr(worker_tasks, "create_database_engine", engine_factory)
    monkeypatch.setattr(worker_tasks, "create_session_factory", session_factory)
    monkeypatch.setattr(worker_tasks, "SqlRunRepository", repository_factory)
    prepare = Mock(return_value=run)
    monkeypatch.setattr(worker_tasks, "prepare_run", prepare)

    prepare_run_task.run(str(uuid4()))
    prepare_run_task.run(str(uuid4()))

    engine_factory.assert_called_once()
    session_factory.assert_called_once_with(engine)
    repository_factory.assert_called_once()
    assert prepare.call_count == 2
    engine.dispose.assert_not_called()


def test_prepare_run_task_failure_still_reuses_process_resources(monkeypatch) -> None:
    engine = Mock()
    repository = Mock()
    run = Mock(id=uuid4(), state=RunState.DISPATCHING)
    engine_factory = Mock(return_value=engine)
    monkeypatch.setattr(
        worker_tasks.Settings,
        "from_env",
        classmethod(lambda _: Settings(database_url="sqlite+pysqlite:///runs.db")),
    )
    monkeypatch.setattr(worker_tasks, "create_database_engine", engine_factory)
    monkeypatch.setattr(worker_tasks, "create_session_factory", Mock())
    monkeypatch.setattr(worker_tasks, "SqlRunRepository", Mock(return_value=repository))
    prepare = Mock(side_effect=[RuntimeError("failed"), run])
    monkeypatch.setattr(worker_tasks, "prepare_run", prepare)

    with pytest.raises(RuntimeError, match="failed"):
        prepare_run_task.run(str(uuid4()))
    prepare_run_task.run(str(uuid4()))

    engine_factory.assert_called_once()
    assert prepare.call_count == 2
    engine.dispose.assert_not_called()


def test_worker_shutdown_disposes_and_clears_process_resources(monkeypatch) -> None:
    first_engine = Mock()
    second_engine = Mock()
    engine_factory = Mock(side_effect=[first_engine, second_engine])
    monkeypatch.setattr(
        worker_tasks.Settings,
        "from_env",
        classmethod(lambda _: Settings(database_url="sqlite+pysqlite:///runs.db")),
    )
    monkeypatch.setattr(worker_tasks, "create_database_engine", engine_factory)
    monkeypatch.setattr(worker_tasks, "create_session_factory", Mock())
    monkeypatch.setattr(
        worker_tasks, "SqlRunRepository", Mock(side_effect=[Mock(), Mock()])
    )
    monkeypatch.setattr(
        worker_tasks,
        "prepare_run",
        Mock(return_value=Mock(id=uuid4(), state=RunState.DISPATCHING)),
    )

    prepare_run_task.run(str(uuid4()))
    worker_tasks.shutdown_worker_process()
    prepare_run_task.run(str(uuid4()))

    first_engine.dispose.assert_called_once_with()
    second_engine.dispose.assert_not_called()
    assert engine_factory.call_count == 2


def test_worker_initialization_failure_is_not_cached_or_leaked(monkeypatch) -> None:
    first_engine = Mock()
    second_engine = Mock()
    marker = "credential-marker"
    engine_factory = Mock(side_effect=[first_engine, second_engine])
    session_factory = Mock(
        side_effect=[RuntimeError(f"invalid configuration: {marker}"), Mock()]
    )
    monkeypatch.setattr(
        worker_tasks.Settings,
        "from_env",
        classmethod(lambda _: Settings(database_url="sqlite+pysqlite:///runs.db")),
    )
    monkeypatch.setattr(worker_tasks, "create_database_engine", engine_factory)
    monkeypatch.setattr(worker_tasks, "create_session_factory", session_factory)
    monkeypatch.setattr(worker_tasks, "SqlRunRepository", Mock(return_value=Mock()))
    monkeypatch.setattr(
        worker_tasks,
        "prepare_run",
        Mock(return_value=Mock(id=uuid4(), state=RunState.DISPATCHING)),
    )

    with pytest.raises(RuntimeError) as captured:
        worker_tasks.initialize_worker_process()
    prepare_run_task.run(str(uuid4()))

    assert marker not in str(captured.value)
    first_engine.dispose.assert_called_once_with()
    second_engine.dispose.assert_not_called()
    assert engine_factory.call_count == 2


def test_celery_uses_safe_reliable_configuration() -> None:
    application = create_celery_app(
        Settings(
            celery_broker_url="memory://",
            result_backend="cache+memory://",
            celery_task_always_eager=True,
            attempt_reaper_interval_seconds=12,
        )
    )

    assert application.conf.task_serializer == "json"
    assert application.conf.result_serializer == "json"
    assert application.conf.accept_content == ["json"]
    assert application.conf.task_acks_late is True
    assert application.conf.task_reject_on_worker_lost is True
    assert application.conf.worker_prefetch_multiplier == 1
    assert application.conf.task_soft_time_limit < application.conf.task_time_limit
    assert application.conf.task_always_eager is True
    assert application.conf.beat_schedule["recover-lost-runner-attempts"] == {
        "task": "smarttest.recover_lost_attempts",
        "schedule": 12,
    }
    assert "smarttest.prepare_run" in celery_app.tasks
    assert "smarttest.recover_lost_attempts" in celery_app.tasks
    assert list(signature(prepare_run_task.run).parameters) == ["run_id"]
    assert list(signature(recover_lost_attempts_task.run).parameters) == []


def test_reaper_interval_must_be_positive() -> None:
    with pytest.raises(ValueError, match="must be positive"):
        Settings.from_env({"ATTEMPT_REAPER_INTERVAL_SECONDS": "0"})


def test_secret_runner_settings_are_bounded() -> None:
    with pytest.raises(ValueError, match="SECRET_CAPABILITY_TTL_SECONDS"):
        Settings.from_env({"SECRET_CAPABILITY_TTL_SECONDS": "301"})
    with pytest.raises(ValueError, match="RUNNER_UID"):
        Settings.from_env({"RUNNER_UID": "-1"})


def test_local_runner_settings_defaults_environment_and_bounds() -> None:
    defaults = Settings.from_env({})
    assert defaults.source_root == ".sources"
    assert defaults.local_runner_enabled is False
    assert defaults.runner_image == "smarttest-runner:local"
    assert defaults.runner_network == "none"
    assert defaults.runner_work_root == ".runner-work"
    assert defaults.runner_max_workers == 2
    assert defaults.runner_poll_interval_seconds == 0.5
    assert defaults.host_execution_enabled is False
    assert defaults.host_project_roots == ()

    configured = Settings.from_env({
        "SOURCE_ROOT": "sources", "LOCAL_RUNNER_ENABLED": "true",
        "RUNNER_IMAGE": "runner:test", "RUNNER_NETWORK": "none",
        "RUNNER_WORK_ROOT": "work", "RUNNER_MAX_WORKERS": "16",
        "RUNNER_POLL_INTERVAL_SECONDS": "0.1",
        "HOST_EXECUTION_ENABLED": "true",
        "HOST_PROJECT_ROOTS": str(Path.cwd()),
    })
    assert configured.local_runner_enabled is True
    assert configured.runner_max_workers == 16
    assert configured.runner_poll_interval_seconds == 0.1
    assert configured.host_execution_enabled is True
    assert configured.host_project_roots == (str(Path.cwd()),)
    with pytest.raises(ValueError, match="RUNNER_MAX_WORKERS"):
        Settings(runner_max_workers=17)
    with pytest.raises(ValueError, match="RUNNER_POLL_INTERVAL_SECONDS"):
        Settings(runner_poll_interval_seconds=0)
    with pytest.raises(ValueError, match="HOST_PROJECT_ROOTS"):
        Settings.from_env({"HOST_EXECUTION_ENABLED": "true"})


def test_recover_lost_attempts_task_reports_count(monkeypatch) -> None:
    repository = Mock()
    repository.expire_attempt_leases.return_value = [Mock(), Mock()]
    monkeypatch.setattr(worker_tasks, "_get_worker_repository", lambda: repository)

    assert recover_lost_attempts_task.run() == {"lost_attempts": 2}
    repository.expire_attempt_leases.assert_called_once_with(None)