from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Event, Lock
from uuid import UUID

from .environment_repository import ConfigurationResolver, EnvironmentRepository
from .environment_sql_repository import SqlEnvironmentRepository
from .load_test_auth import LoadTestAuthResolver
from .load_test_domain import LoadTestRunRecord, LoadTestRunStatus
from .load_test_executor import LoadTestExecutionError, LoadTestExecutor
from .load_test_repository import LoadTestRepository
from .load_test_sql_repository import SqlLoadTestRepository


LoadTestRepositoryLike = LoadTestRepository | SqlLoadTestRepository


class LoadTestService:
    def __init__(self, repository: LoadTestRepositoryLike, executor: LoadTestExecutor,
                 max_active_runs: int = 4, *,
                 environment_repository: EnvironmentRepository | SqlEnvironmentRepository | None = None,
                 configuration_resolver: ConfigurationResolver | None = None) -> None:
        if not 1 <= max_active_runs <= 32:
            raise ValueError("max_active_runs must be between 1 and 32")
        self._repository = repository
        self._executor = executor
        self._auth_resolver = (
            LoadTestAuthResolver(environment_repository, configuration_resolver)
            if environment_repository is not None and configuration_resolver is not None
            else None
        )
        self._pool = ThreadPoolExecutor(
            max_workers=max_active_runs, thread_name_prefix="load-run"
        )
        self._lock = Lock()
        self._cancellations: dict[UUID, Event] = {}
        self._stopped = False

    def recover_interrupted_runs(self) -> int:
        return self._repository.recover_running_runs(
            "Load test execution interrupted by service restart"
        )

    def start_run(self, load_test_id: UUID) -> LoadTestRunRecord:
        definition = self._repository.get_load_test(load_test_id)
        with self._lock:
            if self._stopped:
                raise RuntimeError("Load test execution unavailable")
            run = self._repository.create_run(load_test_id)
            cancellation = Event()
            self._cancellations[run.id] = cancellation
            try:
                self._pool.submit(self._execute, run.id, definition, cancellation)
            except RuntimeError:
                self._cancellations.pop(run.id, None)
                self._fail(run.id, "Load test execution unavailable")
                return self._repository.get_run(run.id)
        return run

    def cancel_run(self, run_id: UUID) -> LoadTestRunRecord:
        run = self._repository.get_run(run_id)
        if run.status != LoadTestRunStatus.RUNNING:
            return run
        with self._lock:
            cancellation = self._cancellations.get(run_id)
            if cancellation is not None:
                cancellation.set()
        return self._repository.get_run(run_id)

    def shutdown(self) -> None:
        with self._lock:
            self._stopped = True
            cancellations = list(self._cancellations.items())
        for run_id, cancellation in cancellations:
            cancellation.set()
        self._pool.shutdown(wait=True, cancel_futures=False)

    def _execute(self, run_id: UUID, definition, cancellation: Event) -> None:
        latest_metrics = self._repository.get_run(run_id).metrics
        snapshot_lock = Lock()

        def snapshot(metrics: dict) -> None:
            nonlocal latest_metrics
            with snapshot_lock:
                if (cancellation.is_set()
                        or metrics["total_requests"] <= latest_metrics["total_requests"]):
                    return
                self._repository.update_run(
                    run_id, status=LoadTestRunStatus.RUNNING, metrics=metrics
                )
                latest_metrics = metrics

        try:
            auth_config = (
                self._auth_resolver.resolve(definition.project_id, definition.environment_id)
                if self._auth_resolver is not None else None
            )
            metrics = self._executor.execute(
                definition, cancel_event=cancellation, snapshot_callback=snapshot,
                auth_config=auth_config,
            )
            status = (LoadTestRunStatus.FAILED if cancellation.is_set()
                      else LoadTestRunStatus.SUCCEEDED)
            self._repository.update_run(
                run_id, status=status, metrics=metrics,
                error_message="Load test execution cancelled" if cancellation.is_set() else None,
                finished_at=datetime.now(timezone.utc),
            )
        except LoadTestExecutionError as exc:
            self._repository.update_run(
                run_id, status=LoadTestRunStatus.FAILED, metrics=latest_metrics,
                error_message=str(exc), finished_at=datetime.now(timezone.utc),
            )
        except Exception:
            self._repository.update_run(
                run_id, status=LoadTestRunStatus.FAILED, metrics=latest_metrics,
                error_message="Load test execution failed",
                finished_at=datetime.now(timezone.utc),
            )
        finally:
            with self._lock:
                self._cancellations.pop(run_id, None)

    def _fail(self, run_id: UUID, message: str) -> None:
        run = self._repository.get_run(run_id)
        self._repository.update_run(
            run_id, status=LoadTestRunStatus.FAILED, metrics=run.metrics,
            error_message=message, finished_at=datetime.now(timezone.utc),
        )