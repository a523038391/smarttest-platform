from threading import Lock
from datetime import datetime
from uuid import UUID

from celery.signals import worker_process_init, worker_process_shutdown
from sqlalchemy import Engine

from services.api.config import Settings
from services.api.database import create_database_engine, create_session_factory
from services.api.domain import InvalidTransition, RunRecord, RunState
from services.api.repository import RunRepository
from services.api.sql_repository import SqlRunRepository

from .celery_app import celery_app


_worker_database_engine: Engine | None = None
_worker_repository: SqlRunRepository | None = None
_worker_resources_lock = Lock()


def _get_worker_repository() -> SqlRunRepository:
    """Return resources created in this process, lazily for solo/eager execution."""
    global _worker_database_engine, _worker_repository

    with _worker_resources_lock:
        if _worker_repository is not None:
            return _worker_repository

        settings = Settings.from_env()
        if not settings.database_url:
            raise RuntimeError("DATABASE_URL is required by smarttest.prepare_run")

        engine: Engine | None = None
        try:
            engine = create_database_engine(settings.database_url)
            repository = SqlRunRepository(create_session_factory(engine))
        except Exception:
            # Do not retain an engine when repository construction is incomplete.
            if engine is not None:
                try:
                    engine.dispose()
                except Exception:
                    pass
            raise RuntimeError("worker database initialization failed") from None

        _worker_database_engine = engine
        _worker_repository = repository
        return repository


@worker_process_init.connect
def initialize_worker_process(**_: object) -> None:
    """Build database resources after Celery forks the worker process."""
    _get_worker_repository()


@worker_process_shutdown.connect
def shutdown_worker_process(**_: object) -> None:
    """Dispose this process's connection pool and clear reusable resources."""
    global _worker_database_engine, _worker_repository

    with _worker_resources_lock:
        engine = _worker_database_engine
        _worker_database_engine = None
        _worker_repository = None
        if engine is not None:
            engine.dispose()


def prepare_run(repository: RunRepository, run_id: UUID | str) -> RunRecord:
    identifier = UUID(str(run_id))
    current = repository.get(identifier)
    if current.state is RunState.CREATED:
        raise InvalidTransition("cannot prepare a run that has not been queued")
    if current.state is not RunState.QUEUED:
        return current
    try:
        return repository.transition(identifier, RunState.DISPATCHING)
    except InvalidTransition:
        current = repository.get(identifier)
        if current.state is not RunState.QUEUED:
            return current
        raise


def retry_run(repository: RunRepository, run_id: UUID | str) -> RunRecord:
    identifier = UUID(str(run_id))
    current = repository.get(identifier)
    if current.state is not RunState.RETRY_WAIT:
        return current
    repository.transition(identifier, RunState.QUEUED)
    return prepare_run(repository, identifier)


@celery_app.task(name="smarttest.prepare_run")
def prepare_run_task(run_id: str) -> dict[str, str]:
    repository = _get_worker_repository()
    run = prepare_run(repository, run_id)
    return {"run_id": str(run.id), "state": run.state.value}


@celery_app.task(name="smarttest.retry_run")
def retry_run_task(run_id: str) -> dict[str, str]:
    run = retry_run(_get_worker_repository(), run_id)
    return {"run_id": str(run.id), "state": run.state.value}


def recover_lost_attempts(
    repository: RunRepository, now: datetime | None = None
) -> int:
    return len(repository.expire_attempt_leases(now))


@celery_app.task(name="smarttest.recover_lost_attempts")
def recover_lost_attempts_task() -> dict[str, int]:
    repository = _get_worker_repository()
    return {"lost_attempts": recover_lost_attempts(repository)}