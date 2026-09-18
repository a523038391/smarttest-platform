from copy import deepcopy
from datetime import datetime, timezone
from threading import RLock
from typing import Any, Sequence
from uuid import UUID, uuid4

from .data_factory_schemas import _bounded_json
from .load_test_domain import (
    LoadTestMode, LoadTestRecord, LoadTestRunRecord, LoadTestRunStatus,
    LoadTestTrafficMode, empty_metrics,
)
from .load_test_schemas import LoadTestTarget


class LoadTestNotFound(LookupError):
    pass


class LoadTestRunNotFound(LookupError):
    pass


class LoadTestConflict(ValueError):
    pass


class LoadTestNameConflict(LoadTestConflict):
    pass


class LoadTestVersionConflict(LoadTestConflict):
    pass


class LoadTestActiveRunConflict(LoadTestConflict):
    pass


def normalize_name(name: str) -> str:
    normalized = name.strip()
    if not normalized or len(normalized) > 255:
        raise ValueError("load test name must contain between 1 and 255 characters")
    return normalized


def normalize_targets(targets: Sequence[Any],
                      traffic_mode: LoadTestTrafficMode = LoadTestTrafficMode.REQUESTS
                      ) -> list[dict[str, Any]]:
    if not 1 <= len(targets) <= 20:
        raise ValueError("load test must contain between 1 and 20 targets")
    normalized = [LoadTestTarget.model_validate(item).model_dump(mode="json") for item in targets]
    if len({item["name"] for item in normalized}) != len(normalized):
        raise ValueError("target names must be unique")
    if (LoadTestTrafficMode(traffic_mode) is not LoadTestTrafficMode.SCENARIO
            and any(item["extractors"] for item in normalized)):
        raise ValueError("extractors are only supported in SCENARIO traffic mode")
    return _bounded_json(normalized)


def normalize_scenario_settings(
    traffic_mode: LoadTestTrafficMode, initial_variables: dict[str, Any] | None,
    stop_on_failure: bool,
) -> tuple[LoadTestTrafficMode, dict[str, Any], bool]:
    normalized_mode = LoadTestTrafficMode(traffic_mode)
    variables = {} if initial_variables is None else initial_variables
    if not isinstance(variables, dict):
        raise ValueError("initial_variables must be a JSON object")
    if not isinstance(stop_on_failure, bool):
        raise ValueError("stop_on_failure must be a boolean")
    return normalized_mode, _bounded_json(variables), stop_on_failure


def validate_settings(mode: LoadTestMode, request_count: int, duration_seconds: int,
                      concurrency: int, interval_ms: int, timeout_seconds: float) -> LoadTestMode:
    normalized_mode = LoadTestMode(mode)
    values = (request_count, duration_seconds, concurrency, interval_ms)
    if any(isinstance(value, bool) or not isinstance(value, int) for value in values):
        raise ValueError("load test numeric settings must be integers")
    if not 1 <= request_count <= 10_000:
        raise ValueError("request_count must be between 1 and 10000")
    if not 1 <= duration_seconds <= 1_800:
        raise ValueError("duration_seconds must be between 1 and 1800")
    if not 1 <= concurrency <= 100:
        raise ValueError("concurrency must be between 1 and 100")
    if not 0 <= interval_ms <= 60_000:
        raise ValueError("interval_ms must be between 0 and 60000")
    if (isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not 0 < timeout_seconds <= 120):
        raise ValueError("timeout_seconds must be greater than 0 and at most 120")
    return normalized_mode


class LoadTestRepository:
    def __init__(self) -> None:
        self._lock = RLock()
        self._definitions: dict[UUID, LoadTestRecord] = {}
        self._names: dict[tuple[UUID, str], UUID] = {}
        self._runs: dict[UUID, LoadTestRunRecord] = {}

    def create_load_test(self, project_id: UUID, name: str, description: str,
                         targets: Sequence[Any], mode: LoadTestMode, request_count: int,
                         duration_seconds: int, concurrency: int, interval_ms: int,
                         timeout_seconds: float,
                         environment_id: UUID | None = None,
                         traffic_mode: LoadTestTrafficMode = LoadTestTrafficMode.REQUESTS,
                         initial_variables: dict[str, Any] | None = None,
                         stop_on_failure: bool = True) -> LoadTestRecord:
        traffic_mode, initial_variables, stop_on_failure = normalize_scenario_settings(
            traffic_mode, initial_variables, stop_on_failure,
        )
        normalized_name = normalize_name(name)
        normalized_targets = normalize_targets(targets, traffic_mode)
        mode = validate_settings(mode, request_count, duration_seconds, concurrency,
                                 interval_ms, timeout_seconds)
        with self._lock:
            key = (project_id, normalized_name)
            if key in self._names:
                raise LoadTestNameConflict("a load test with this project and name exists")
            now = datetime.now(timezone.utc)
            record = LoadTestRecord(
                id=uuid4(), project_id=project_id, name=normalized_name,
                description=description, targets=normalized_targets, mode=mode,
                request_count=request_count, duration_seconds=duration_seconds,
                concurrency=concurrency, interval_ms=interval_ms,
                timeout_seconds=timeout_seconds, state_version=0,
                created_at=now, updated_at=now, environment_id=environment_id,
                traffic_mode=traffic_mode, initial_variables=initial_variables,
                stop_on_failure=stop_on_failure,
            )
            self._definitions[record.id] = record
            self._names[key] = record.id
            return deepcopy(record)

    def get_load_test(self, load_test_id: UUID) -> LoadTestRecord:
        with self._lock:
            try:
                return deepcopy(self._definitions[load_test_id])
            except KeyError:
                raise LoadTestNotFound(f"load test {load_test_id} was not found") from None

    def list_load_tests(self, project_id: UUID) -> list[LoadTestRecord]:
        with self._lock:
            items = [item for item in self._definitions.values() if item.project_id == project_id]
            return deepcopy(sorted(items, key=lambda item: item.created_at))

    def update_load_test(self, load_test_id: UUID, *, name: str,
                         description: str, targets: Sequence[Any], mode: LoadTestMode,
                         request_count: int, duration_seconds: int, concurrency: int,
                         interval_ms: int, timeout_seconds: float,
                         expected_version: int,
                         environment_id: UUID | None = None,
                         traffic_mode: LoadTestTrafficMode = LoadTestTrafficMode.REQUESTS,
                         initial_variables: dict[str, Any] | None = None,
                         stop_on_failure: bool = True) -> LoadTestRecord:
        traffic_mode, initial_variables, stop_on_failure = normalize_scenario_settings(
            traffic_mode, initial_variables, stop_on_failure,
        )
        normalized_name = normalize_name(name)
        normalized_targets = normalize_targets(targets, traffic_mode)
        mode = validate_settings(mode, request_count, duration_seconds, concurrency,
                                 interval_ms, timeout_seconds)
        with self._lock:
            current = self.get_load_test(load_test_id)
            if current.state_version != expected_version:
                raise LoadTestVersionConflict("load test state version mismatch")
            key = (current.project_id, normalized_name)
            if self._names.get(key) not in {None, load_test_id}:
                raise LoadTestNameConflict("a load test with this project and name exists")
            updated = current.updated(
                name=normalized_name, description=description, targets=normalized_targets,
                mode=mode, request_count=request_count,
                duration_seconds=duration_seconds, concurrency=concurrency,
                interval_ms=interval_ms, timeout_seconds=timeout_seconds,
                environment_id=environment_id,
                traffic_mode=traffic_mode, initial_variables=initial_variables,
                stop_on_failure=stop_on_failure,
            )
            self._definitions[load_test_id] = updated
            self._names.pop((current.project_id, current.name), None)
            self._names[key] = load_test_id
            return deepcopy(updated)

    def delete_load_test(self, load_test_id: UUID) -> None:
        with self._lock:
            current = self.get_load_test(load_test_id)
            if self._active_run(load_test_id) is not None:
                raise LoadTestActiveRunConflict("load test has an active run")
            del self._definitions[load_test_id]
            self._names.pop((current.project_id, current.name), None)
            self._runs = {key: run for key, run in self._runs.items()
                          if run.load_test_id != load_test_id}

    def create_run(self, load_test_id: UUID) -> LoadTestRunRecord:
        with self._lock:
            definition = self.get_load_test(load_test_id)
            if self._active_run(load_test_id) is not None:
                raise LoadTestActiveRunConflict("load test already has an active run")
            record = LoadTestRunRecord(
                uuid4(), load_test_id, LoadTestRunStatus.RUNNING, definition.mode,
                datetime.now(timezone.utc), None,
                empty_metrics([target["name"] for target in definition.targets]), None,
            )
            self._runs[record.id] = record
            return deepcopy(record)

    def update_run(self, run_id: UUID, *, status: LoadTestRunStatus,
                   metrics: dict[str, Any], error_message: str | None = None,
                   finished_at: datetime | None = None) -> LoadTestRunRecord:
        with self._lock:
            current = self.get_run(run_id)
            if current.status != LoadTestRunStatus.RUNNING:
                return current
            updated = LoadTestRunRecord(
                current.id, current.load_test_id, LoadTestRunStatus(status), current.mode,
                current.started_at, finished_at, deepcopy(metrics), error_message,
            )
            self._runs[run_id] = updated
            return deepcopy(updated)

    def recover_running_runs(self, message: str) -> int:
        with self._lock:
            now = datetime.now(timezone.utc)
            running_ids = [run_id for run_id, run in self._runs.items()
                           if run.status == LoadTestRunStatus.RUNNING]
            for run_id in running_ids:
                run = self._runs[run_id]
                self._runs[run_id] = LoadTestRunRecord(
                    run.id, run.load_test_id, LoadTestRunStatus.FAILED, run.mode,
                    run.started_at, now, deepcopy(run.metrics), message,
                )
            return len(running_ids)

    def get_run(self, run_id: UUID) -> LoadTestRunRecord:
        with self._lock:
            try:
                return deepcopy(self._runs[run_id])
            except KeyError:
                raise LoadTestRunNotFound(f"load test run {run_id} was not found") from None

    def list_runs(self, load_test_id: UUID) -> list[LoadTestRunRecord]:
        with self._lock:
            self.get_load_test(load_test_id)
            items = [run for run in self._runs.values() if run.load_test_id == load_test_id]
            return deepcopy(sorted(items, key=lambda item: item.started_at, reverse=True))

    def _active_run(self, load_test_id: UUID) -> LoadTestRunRecord | None:
        return next((run for run in self._runs.values()
                     if run.load_test_id == load_test_id
                     and run.status == LoadTestRunStatus.RUNNING), None)