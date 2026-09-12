"""Node-local polling service that executes database-dispatched runs."""
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
import logging
from pathlib import Path
import shutil
from tempfile import mkdtemp
from threading import Event, Lock, Thread
from uuid import UUID, uuid4

from packages.protocol import Engine
from services.api.domain import RUN_TERMINAL, RunState
from services.api.execution_coordinator import ExecutionBatchCoordinator
from services.api.repository import RunRepository
from services.api.test_plan_repository import (
    RunSpecTaskMaterializer,
    TestPlanRepository,
)
from services.artifacts import ArtifactCollector, ArtifactStore
from services.docker_executor import DockerExecutor
from services.host_executor import HostExecutor
from services.runner_manager import RunnerExecutor, RunnerManager
from services.source_store import LocalSourceStore


ExecutorFactory = Callable[[Path, Path], RunnerExecutor]
HostExecutorFactory = Callable[[Path, Path, Path], RunnerExecutor]
logger = logging.getLogger(__name__)


class LocalRunnerService:
    """Poll DISPATCHING runs and execute each run at most once concurrently."""

    def __init__(
        self,
        repository: RunRepository,
        plans: TestPlanRepository,
        dispatcher: object,
        source_store: LocalSourceStore,
        artifact_store: ArtifactStore,
        work_root: Path | str,
        *,
        image: str = "smarttest-runner:local",
        network: str = "none",
        max_workers: int = 2,
        poll_interval_seconds: float = 0.5,
        executor_factory: ExecutorFactory | None = None,
        host_executor_factory: HostExecutorFactory | None = None,
    ) -> None:
        if not 1 <= max_workers <= 16:
            raise ValueError("max_workers must be between 1 and 16")
        if poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be positive")
        self.repository = repository
        self.plans = plans
        self.source_store = source_store
        self.work_root = Path(work_root).resolve()
        self.artifact_spool_root = self.work_root / "artifact-spool"
        self.max_workers = max_workers
        self.poll_interval_seconds = poll_interval_seconds
        self._image = image
        self._network = network
        self._executor_factory = executor_factory or self._docker_executor
        self._host_executor_factory = host_executor_factory or self._host_executor
        self._host_locks: dict[str, Lock] = {}
        self._coordinator = ExecutionBatchCoordinator(plans, repository, dispatcher)
        self._materializer = RunSpecTaskMaterializer(plans)
        self._collector = ArtifactCollector(
            repository, artifact_store, self.artifact_spool_root
        )
        self._stop = Event()
        self._lock = Lock()
        self._submitted: set[UUID] = set()
        self._pool: ThreadPoolExecutor | None = None
        self._thread: Thread | None = None

    @property
    def running(self) -> bool:
        with self._lock:
            return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self.work_root.mkdir(parents=True, exist_ok=True)
            self.artifact_spool_root.mkdir(parents=True, exist_ok=True)
            self._stop.clear()
            self._pool = ThreadPoolExecutor(
                max_workers=self.max_workers, thread_name_prefix="local-runner"
            )
            self._thread = Thread(
                target=self._poll_loop, name="local-runner-poller", daemon=True
            )
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            thread, pool = self._thread, self._pool
        if thread is not None:
            thread.join()
        if pool is not None:
            pool.shutdown(wait=True, cancel_futures=False)
        with self._lock:
            self._thread = None
            self._pool = None
            self._submitted.clear()

    def poll_once(self) -> int:
        """Submit newly observed runs; exposed for deterministic service tests."""
        try:
            dispatching = [
                run for run in self.repository.list()
                if run.state is RunState.DISPATCHING
            ]
        except Exception:
            return 0
        submitted = 0
        for run in dispatching:
            with self._lock:
                pool = self._pool
                if pool is None or run.id in self._submitted:
                    continue
                self._submitted.add(run.id)
            try:
                future = pool.submit(self._execute_run, run.id)
            except Exception:
                with self._lock:
                    self._submitted.discard(run.id)
                self._converge_infra_error(run.id)
                continue
            future.add_done_callback(
                lambda completed, run_id=run.id: self._completed(run_id, completed)
            )
            submitted += 1
        return submitted

    def _poll_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.poll_once()
            except Exception:
                pass
            self._stop.wait(self.poll_interval_seconds)

    def _execute_run(self, run_id: UUID) -> None:
        temporary_workspace: Path | None = None
        stage = "run_spec"
        try:
            spec = self.plans.get_run_spec(run_id)
            stage = "source"
            host_lock: Lock | None = None
            if spec.source_ref.startswith("host-source:"):
                if spec.engine not in {Engine.PYTEST, Engine.PLAYWRIGHT}:
                    raise ValueError("host source has an unsupported engine")
                host = self.source_store.resolve_host_reference(
                    spec.project_id, spec.source_ref, spec.content_digest, spec.entrypoint
                )
                workspace = host.project_directory
                executor = self._host_executor_factory(
                    workspace, host.python_executable, self.artifact_spool_root
                )
                host_lock = self._host_project_lock(workspace)
            else:
                temporary_workspace = Path(
                    mkdtemp(prefix=f"run-{run_id}-", dir=self.work_root)
                )
                workspace = temporary_workspace
                self.source_store.materialize(
                    spec.project_id, spec.source_ref, spec.content_digest,
                    spec.entrypoint, workspace,
                )
                executor = self._executor_factory(workspace, self.artifact_spool_root)
            stage = "task"
            task = self._materializer.materialize(run_id, uuid4())
            stage = "runner"
            manager = RunnerManager(
                self.repository, executor, artifact_collector=self._collector,
                completion_callback=self._coordinator.on_run_finished,
            )
            if host_lock is None:
                manager.execute(task)
            else:
                with host_lock:
                    manager.execute(task)
        except Exception as exc:
            logger.error(
                "local runner execution failed: run_id=%s stage=%s error_type=%s",
                run_id, stage, type(exc).__name__,
            )
            self._converge_infra_error(run_id)
        finally:
            if temporary_workspace is not None:
                shutil.rmtree(temporary_workspace, ignore_errors=True)

    def _converge_infra_error(self, run_id: UUID) -> None:
        try:
            current = self.repository.get(run_id)
            if current.state not in RUN_TERMINAL and current.state is not RunState.CREATED:
                self.repository.transition(run_id, RunState.INFRA_ERROR)
        except Exception:
            pass
        try:
            self._coordinator.on_run_finished(run_id)
        except Exception:
            pass

    def _completed(self, run_id: UUID, _: Future[None]) -> None:
        with self._lock:
            self._submitted.discard(run_id)

    def _docker_executor(self, workspace: Path, artifact_spool_root: Path) -> RunnerExecutor:
        return DockerExecutor(
            self._image,
            workspace,
            network=self._network,
            artifact_spool_root=artifact_spool_root,
        )

    @staticmethod
    def _host_executor(
        workspace: Path, python_executable: Path, artifact_spool_root: Path
    ) -> RunnerExecutor:
        return HostExecutor(workspace, python_executable, artifact_spool_root)

    def _host_project_lock(self, workspace: Path) -> Lock:
        key = str(workspace).casefold()
        with self._lock:
            return self._host_locks.setdefault(key, Lock())


__all__ = ["ExecutorFactory", "HostExecutorFactory", "LocalRunnerService"]