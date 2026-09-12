"""Container-local runner agent."""

from collections.abc import Callable, Mapping
import mimetypes
from pathlib import Path
from threading import Event, Lock, Thread
from time import monotonic
from typing import Any
from uuid import uuid4

from packages.protocol import (
    Engine,
    EventEnvelope,
    EventType,
    ResultEnvelope,
    ResultOutcome,
    TaskEnvelope,
)

from .adapters import Adapter, AdapterResult, HTTPAdapter, PlaywrightAdapter, PytestAdapter

EventSink = Callable[[EventEnvelope], None]


class RunnerAgent:
    """Select an adapter, emit ordered events, and return the final envelope."""

    def __init__(
        self,
        task: TaskEnvelope,
        workspace: Path,
        event_sink: EventSink,
        adapters: Mapping[Engine | str, Adapter] | None = None,
        heartbeat_interval: float = 30.0,
        artifact_root: Path | None = None,
    ) -> None:
        self.task = task
        self.workspace = Path(workspace)
        self.event_sink = event_sink
        defaults: dict[Engine, Adapter] = {
            Engine.HTTP: HTTPAdapter(),
            Engine.PYTEST: PytestAdapter(),
            Engine.PLAYWRIGHT: PlaywrightAdapter(),
        }
        if adapters:
            defaults.update({Engine(key): value for key, value in adapters.items()})
        self.adapters = defaults
        self._seq = 0
        if heartbeat_interval <= 0:
            raise ValueError("heartbeat_interval must be positive")
        self.heartbeat_interval = heartbeat_interval
        self._emit_lock = Lock()
        self._heartbeat_stop = Event()
        self._heartbeat_error: Exception | None = None
        self.artifact_root = artifact_root

    def run(self) -> ResultEnvelope:
        started = monotonic()
        self._emit(EventType.STARTED, {"engine": self.task.engine.value})
        heartbeat = Thread(target=self._heartbeat_loop, daemon=True)
        heartbeat.start()
        try:
            result = self.adapters[self.task.engine].run(self.task, self.workspace)
        except Exception as exc:  # Adapter boundaries must become protocol results.
            result = AdapterResult(
                ResultOutcome.INFRA_ERROR,
                "Adapter raised an unexpected error",
                {"error_type": type(exc).__name__},
            )
        try:
            if result.stdout:
                self._emit(EventType.LOG, {"stream": "stdout", "message": result.stdout})
            if result.stderr:
                self._emit(EventType.LOG, {"stream": "stderr", "message": result.stderr})
            self._emit_artifacts()
            assertion_results = result.details.get("assertions")
            self._emit(
                EventType.ASSERTION,
                {
                    "passed": result.outcome is ResultOutcome.SUCCEEDED,
                    "message": result.message,
                    **({"results": assertion_results} if assertion_results is not None else {}),
                },
            )
            extraction_names = result.details.get("extractions")
            if extraction_names:
                self._emit(EventType.EXTRACTION, {"names": extraction_names})
            if result.outcome is not ResultOutcome.SUCCEEDED:
                self._emit(
                    EventType.ERROR,
                    {"message": result.message, **dict(result.details)},
                )
            self._emit(EventType.FINISHED, {"outcome": result.outcome.value})
        finally:
            self._heartbeat_stop.set()
            heartbeat.join(timeout=min(self.heartbeat_interval, 1.0) + 0.1)

        if self._heartbeat_error is not None:
            raise RuntimeError("heartbeat delivery failed") from self._heartbeat_error

        duration_ms = max(0, int((monotonic() - started) * 1000))
        summary: dict[str, Any] = {"message": result.message, **dict(result.details)}
        if result.exit_code is not None:
            summary["exit_code"] = result.exit_code
        summary["timed_out"] = result.timed_out
        return ResultEnvelope(
            run_id=self.task.run_id,
            attempt_id=self.task.attempt_id,
            engine=self.task.engine,
            outcome=result.outcome,
            duration_ms=duration_ms,
            summary=summary,
        )

    def _emit(self, event_type: EventType, payload: Mapping[str, Any]) -> None:
        with self._emit_lock:
            event = EventEnvelope(
                run_id=self.task.run_id,
                attempt_id=self.task.attempt_id,
                engine=self.task.engine,
                event_id=uuid4(),
                seq=self._seq,
                type=event_type,
                payload=payload,
            )
            self.event_sink(event)
            self._seq += 1

    def _heartbeat_loop(self) -> None:
        while not self._heartbeat_stop.wait(self.heartbeat_interval):
            try:
                self._emit(EventType.HEARTBEAT, {})
            except Exception as exc:
                self._heartbeat_error = exc
                self._heartbeat_stop.set()
                return

    def _emit_artifacts(self) -> None:
        if self.artifact_root is None or not self.artifact_root.is_dir():
            return
        root = self.artifact_root.resolve()
        files = [path for path in root.rglob("*") if path.is_file() and not path.is_symlink()]
        if len(files) > 100:
            raise RuntimeError("artifact count exceeds the configured limit")
        total = 0
        for path in sorted(files):
            size = path.stat().st_size
            total += size
            if total > 100 * 1024 * 1024:
                raise RuntimeError("artifact bytes exceed the configured limit")
            relative = path.resolve().relative_to(root).as_posix()
            content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            event_type = (
                EventType.SCREENSHOT if content_type.startswith("image/")
                else EventType.ARTIFACT
            )
            self._emit(event_type, {
                "relative_path": relative,
                "file_name": path.name,
                "artifact_type": "screenshot" if event_type is EventType.SCREENSHOT else "file",
                "size_bytes": size,
            })