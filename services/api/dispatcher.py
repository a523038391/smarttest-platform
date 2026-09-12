from typing import Protocol
from uuid import UUID

from .domain import InvalidTransition, RunState
from .repository import RunRepository


class DispatchUnavailable(RuntimeError):
    pass


class CeleryClient(Protocol):
    def send_task(self, name: str, args: list[str], **options: object) -> object: ...


class RunDispatcher(Protocol):
    def dispatch(self, run_id: UUID) -> None: ...


class CeleryRunDispatcher:
    def __init__(self, celery: CeleryClient | None = None) -> None:
        if celery is None:
            from workers.celery_app import celery_app

            celery = celery_app
        self._celery = celery

    def dispatch(self, run_id: UUID) -> None:
        self._celery.send_task("smarttest.prepare_run", args=[str(run_id)])


class DatabaseRunDispatcher:
    """Advance queued runs for consumption by the node-local runner service."""

    def __init__(self, repository: RunRepository) -> None:
        self._repository = repository

    def dispatch(self, run_id: UUID) -> None:
        current = self._repository.get(run_id)
        if current.state is RunState.CREATED:
            raise InvalidTransition("cannot dispatch a run that has not been queued")
        if current.state is not RunState.QUEUED:
            return
        try:
            self._repository.transition(run_id, RunState.DISPATCHING)
        except InvalidTransition:
            current = self._repository.get(run_id)
            if current.state is not RunState.QUEUED:
                return
            raise


class CeleryRetryScheduler:
    def __init__(self, celery: CeleryClient | None = None) -> None:
        if celery is None:
            from workers.celery_app import celery_app

            celery = celery_app
        self._celery = celery

    def schedule(self, run_id: UUID, delay_seconds: int) -> None:
        self._celery.send_task(
            "smarttest.retry_run", args=[str(run_id)], countdown=delay_seconds
        )


__all__ = [
    "CeleryRetryScheduler", "CeleryRunDispatcher", "DatabaseRunDispatcher",
    "DispatchUnavailable", "RunDispatcher",
]