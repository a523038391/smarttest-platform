"""Bounded dispatch for immutable test-plan execution batches."""
from dataclasses import dataclass
from uuid import UUID

from .dispatcher import RunDispatcher
from .domain import RunState
from .repository import RunRepository
from .test_plan_repository import TestPlanRepository


OCCUPIED_STATES = frozenset({
    RunState.QUEUED, RunState.DISPATCHING, RunState.RUNNING,
    RunState.CANCELLING, RunState.RETRY_WAIT,
})


@dataclass(frozen=True, slots=True)
class DispatchResult:
    dispatched: tuple[UUID, ...]
    failed: tuple[UUID, ...]


class ExecutionBatchCoordinator:
    def __init__(
        self, plans: TestPlanRepository, runs: RunRepository, dispatcher: RunDispatcher,
    ) -> None:
        self._plans = plans
        self._runs = runs
        self._dispatcher = dispatcher

    def dispatch_available(self, batch_id: UUID) -> DispatchResult:
        dispatched: list[UUID] = []
        failed: list[UUID] = []
        while True:
            claimed = self._plans.claim_available_runs(batch_id)
            if not claimed:
                break
            refill = False
            for run_id in claimed:
                try:
                    self._dispatcher.dispatch(run_id)
                except Exception:
                    self._runs.transition(run_id, RunState.INFRA_ERROR)
                    failed.append(run_id)
                    refill = True
                    continue
                dispatched.append(run_id)
            if not refill:
                break
        return DispatchResult(tuple(dispatched), tuple(failed))

    def on_run_finished(self, run_id: UUID) -> DispatchResult:
        spec = self._plans.get_run_spec(run_id)
        return self.dispatch_available(spec.batch_id)