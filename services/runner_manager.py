"""Runner lifecycle coordination and durable execution recording."""
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Protocol
from uuid import UUID

from packages.protocol import (
    Engine,
    EventEnvelope,
    EventType,
    ResultEnvelope,
    ResultOutcome,
    TaskEnvelope,
)
from runner import Adapter, EventSink, RunnerAgent
from services.artifacts import ArtifactCollector
from services.api.domain import AttemptState, InvalidTransition, RunState
from services.api.repository import RunRepository
from services.retry_policy import decide_retry


class RunnerIdentityError(ValueError):
    """A protocol envelope does not identify the managed execution."""


class RunnerExecutor(Protocol):
    """Replaceable boundary implemented by DockerExecutor in production."""

    def execute(self, task: TaskEnvelope, event_sink: EventSink) -> ResultEnvelope:
        """Execute one task and synchronously deliver its events."""


class InProcessRunnerExecutor:
    """Run RunnerAgent in this process; intended only for tests and development."""

    def __init__(
        self,
        workspace: Path | str,
        adapters: Mapping[Engine | str, Adapter] | None = None,
    ) -> None:
        self.workspace = Path(workspace)
        self.adapters = adapters

    def execute(self, task: TaskEnvelope, event_sink: EventSink) -> ResultEnvelope:
        return RunnerAgent(
            task, self.workspace, event_sink, self.adapters
        ).run()


ExecutorCallable = Callable[[TaskEnvelope, EventSink], ResultEnvelope]


class RetryScheduler(Protocol):
    def schedule(self, run_id: UUID, delay_seconds: int) -> None: ...


class RunnerManager:
    """Validate, execute, and persist one TaskEnvelope lifecycle."""

    _RESULT_STATES = {
        ResultOutcome.SUCCEEDED: (AttemptState.SUCCEEDED, RunState.SUCCEEDED),
        ResultOutcome.FAILED: (AttemptState.FAILED, RunState.FAILED),
        ResultOutcome.CANCELLED: (AttemptState.CANCELLED, RunState.CANCELLED),
        ResultOutcome.TIMED_OUT: (AttemptState.TIMED_OUT, RunState.TIMED_OUT),
        ResultOutcome.INFRA_ERROR: (AttemptState.INFRA_ERROR, RunState.INFRA_ERROR),
    }

    def __init__(
        self,
        repository: RunRepository,
        executor: RunnerExecutor | ExecutorCallable,
        runner_id: str = "runner-local",
        lease_seconds: int = 300,
        artifact_collector: ArtifactCollector | None = None,
        retry_scheduler: RetryScheduler | None = None,
        completion_callback: Callable[[UUID], None] | None = None,
    ) -> None:
        self.repository = repository
        self.executor = executor
        self.runner_id = runner_id
        self.lease_seconds = lease_seconds
        self.artifact_collector = artifact_collector
        self.retry_scheduler = retry_scheduler
        self.completion_callback = completion_callback

    def execute(self, task: TaskEnvelope) -> ResultEnvelope:
        run = self.repository.get(task.run_id)
        if run.engine is not task.engine:
            raise RunnerIdentityError("task engine does not match its run")
        if run.state is not RunState.DISPATCHING:
            raise RunnerIdentityError(f"run cannot execute from {run.state.value}")

        try:
            _, attempt = self.repository.begin_attempt(
                task.run_id, task.attempt_id, self.runner_id, self.lease_seconds
            )
        except InvalidTransition as exc:
            raise RunnerIdentityError("run was already claimed by another attempt") from exc
        if attempt.id != task.attempt_id or attempt.run_id != task.run_id:
            raise RunnerIdentityError("created attempt does not match the task")

        for state in (AttemptState.PREPARING, AttemptState.RUNNING):
            self.repository.transition_attempt(task.attempt_id, state)

        def persist_event(event: EventEnvelope) -> None:
            self._validate_event(task, event)
            if event.type in {EventType.ARTIFACT, EventType.SCREENSHOT}:
                if self.artifact_collector is None:
                    raise RunnerIdentityError("artifact collector is not configured")
                event = self.artifact_collector.collect(task, event)
            if event.type is EventType.HEARTBEAT:
                self.repository.renew_attempt_lease(
                    task.attempt_id, self.runner_id, self.lease_seconds
                )
            self.repository.append_event(event)

        identity_error: RunnerIdentityError | None = None
        try:
            execute = getattr(self.executor, "execute", self.executor)
            result = execute(task, persist_event)
            self._validate_result(task, result)
        except RunnerIdentityError as exc:
            identity_error = exc
            result = self._infra_result(task, exc)
        except Exception as exc:
            result = self._infra_result(task, exc)

        if (
            result.outcome is ResultOutcome.SUCCEEDED
            and self.repository.get(task.run_id).state is RunState.CANCELLING
        ):
            result = self._cancelled_result(task, result)
        current_attempt = self.repository.get_attempt(task.attempt_id)
        if current_attempt.state is AttemptState.LOST:
            return self._lost_result(task)
        attempt_state, run_state = self._RESULT_STATES[result.outcome]
        try:
            self.repository.transition_attempt(task.attempt_id, AttemptState.COLLECTING)
            self.repository.complete_attempt(task.attempt_id, attempt_state, result)
        except InvalidTransition:
            if self.repository.get_attempt(task.attempt_id).state is AttemptState.LOST:
                return self._lost_result(task)
            raise
        decision = decide_retry(task, result, len(self.repository.list_attempts(task.run_id)))
        if decision.should_retry:
            if self.retry_scheduler is None:
                self.repository.transition(task.run_id, RunState.INFRA_ERROR)
                self._notify_completion(task.run_id)
                return result
            self.repository.transition(task.run_id, RunState.RETRY_WAIT)
            try:
                self.retry_scheduler.schedule(task.run_id, decision.delay_seconds)
            except Exception:
                self.repository.transition(task.run_id, RunState.INFRA_ERROR)
                self._notify_completion(task.run_id)
            return result
        if run_state is RunState.CANCELLED:
            current = self.repository.get(task.run_id)
            if current.state is not RunState.CANCELLING:
                self.repository.transition(task.run_id, RunState.CANCELLING)
        self.repository.transition(task.run_id, run_state)
        self._notify_completion(task.run_id)
        if identity_error is not None:
            raise identity_error
        return result

    run = execute

    def _notify_completion(self, run_id: UUID) -> None:
        if self.completion_callback is None:
            return
        try:
            self.completion_callback(run_id)
        except Exception:
            # A terminal result is already durable; capacity reconciliation can be retried.
            return

    @staticmethod
    def _validate_event(task: TaskEnvelope, event: EventEnvelope) -> None:
        if (
            event.run_id != task.run_id
            or event.attempt_id != task.attempt_id
            or event.engine is not task.engine
        ):
            raise RunnerIdentityError("event identity does not match the task")

    @staticmethod
    def _validate_result(task: TaskEnvelope, result: ResultEnvelope) -> None:
        if (
            result.run_id != task.run_id
            or result.attempt_id != task.attempt_id
            or result.engine is not task.engine
        ):
            raise RunnerIdentityError("result identity does not match the task")

    @staticmethod
    def _infra_result(task: TaskEnvelope, exc: Exception) -> ResultEnvelope:
        return ResultEnvelope(
            run_id=task.run_id,
            attempt_id=task.attempt_id,
            engine=task.engine,
            outcome=ResultOutcome.INFRA_ERROR,
            duration_ms=0,
            summary={"error_type": type(exc).__name__},
        )

    @staticmethod
    def _cancelled_result(
        task: TaskEnvelope, original: ResultEnvelope
    ) -> ResultEnvelope:
        return ResultEnvelope(
            run_id=task.run_id,
            attempt_id=task.attempt_id,
            engine=task.engine,
            outcome=ResultOutcome.CANCELLED,
            completed_at=original.completed_at,
            duration_ms=original.duration_ms,
            summary={**dict(original.summary), "cancel_requested": True},
        )

    @staticmethod
    def _lost_result(task: TaskEnvelope) -> ResultEnvelope:
        return ResultEnvelope(
            run_id=task.run_id,
            attempt_id=task.attempt_id,
            engine=task.engine,
            outcome=ResultOutcome.INFRA_ERROR,
            duration_ms=0,
            summary={"reason": "runner_lease_expired"},
        )


__all__ = [
    "InProcessRunnerExecutor",
    "RunnerExecutor",
    "RunnerIdentityError",
    "RunnerManager",
    "RetryScheduler",
]