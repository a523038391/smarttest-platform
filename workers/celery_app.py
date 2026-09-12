from celery import Celery

from services.api.config import Settings


def create_celery_app(settings: Settings | None = None) -> Celery:
    resolved = settings or Settings.from_env()
    application = Celery(
        "smarttest",
        broker=resolved.celery_broker_url,
        backend=resolved.result_backend,
        include=["workers.tasks"],
    )
    application.conf.update(
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        worker_prefetch_multiplier=1,
        task_soft_time_limit=300,
        task_time_limit=330,
        task_always_eager=resolved.celery_task_always_eager,
        task_eager_propagates=True,
        beat_schedule={
            "recover-lost-runner-attempts": {
                "task": "smarttest.recover_lost_attempts",
                "schedule": resolved.attempt_reaper_interval_seconds,
            }
        },
    )
    return application


celery_app = create_celery_app()