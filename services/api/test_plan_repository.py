import json
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from threading import RLock
from typing import Any, Protocol, Sequence
from uuid import UUID, NAMESPACE_URL, uuid4, uuid5

from packages.protocol import Engine, SecretReference, TaskEnvelope

from .automation_domain import AutomationScriptRecord, AutomationScriptStatus
from .domain import RunRecord, RunState
from .environment_domain import ConfigurationCategory, EnvironmentRecord, EnvironmentStatus
from .repository import RunRepository
from .test_plan_domain import (
    ExecutionBatchRecord, RunSpecRecord, TestPlanDataRow, TestPlanItem,
    TestPlanRecord, TestPlanStatus,
)

MAX_PLAN_ITEMS = 100
MAX_PLAN_ROWS = 1_000
MAX_JSON_MAP_BYTES = 65_536


class TestPlanNotFound(LookupError):
    pass


class RunSpecNotFound(LookupError):
    pass


class TestPlanConflict(ValueError):
    pass


class TestPlanVersionConflict(TestPlanConflict):
    pass


class AutomationRevisionReader(Protocol):
    def get_revision(self, script_id: UUID, revision: int) -> AutomationScriptRecord: ...


class EnvironmentRevisionReader(Protocol):
    def get_revision(self, environment_id: UUID, revision: int) -> EnvironmentRecord: ...


class RunSpecReader(Protocol):
    def get_run_spec(self, run_id: UUID) -> RunSpecRecord: ...


def _json_object(value: dict[str, Any], label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")

    def validate(item: Any) -> None:
        if item is None or isinstance(item, (str, bool, int)):
            return
        if isinstance(item, float):
            if item != item or item in (float("inf"), float("-inf")):
                raise ValueError
            return
        if isinstance(item, list):
            for child in item:
                validate(child)
            return
        if isinstance(item, dict) and all(isinstance(key, str) for key in item):
            for child in item.values():
                validate(child)
            return
        raise ValueError

    try:
        validate(value)
        encoded = json.dumps(
            value, ensure_ascii=False, allow_nan=False, separators=(",", ":")
        ).encode("utf-8")
    except (RecursionError, TypeError, ValueError):
        raise ValueError(f"{label} must contain only JSON values") from None
    if len(encoded) > MAX_JSON_MAP_BYTES:
        raise ValueError(f"{label} cannot exceed {MAX_JSON_MAP_BYTES} bytes")
    return deepcopy(value)


def normalize_plan_items(items: Sequence[TestPlanItem]) -> tuple[TestPlanItem, ...]:
    if not items:
        raise ValueError("a test plan must contain at least one item")
    if len(items) > MAX_PLAN_ITEMS:
        raise ValueError(f"at most {MAX_PLAN_ITEMS} test plan items are allowed")
    item_ids: set[UUID] = set()
    total_rows = 0
    normalized: list[TestPlanItem] = []
    for item in items:
        if item.item_id in item_ids:
            raise ValueError("test plan item IDs must be unique")
        item_ids.add(item.item_id)
        if item.script_revision < 1:
            raise ValueError("script_revision must be positive")
        if (item.environment_id is None) != (item.environment_revision is None):
            raise ValueError("environment_id and environment_revision must be provided together")
        if item.environment_revision is not None and item.environment_revision < 1:
            raise ValueError("environment_revision must be positive")
        rows = item.data_rows or (TestPlanDataRow("default", {}),)
        row_keys: set[str] = set()
        normalized_rows: list[TestPlanDataRow] = []
        for row in rows:
            key = row.row_key.strip()
            if not key or len(key) > 255:
                raise ValueError("row_key must contain 1 to 255 characters")
            if key in row_keys:
                raise ValueError(f"duplicate row_key: {key}")
            row_keys.add(key)
            normalized_rows.append(TestPlanDataRow(key, _json_object(row.values, "row values")))
        total_rows += len(normalized_rows)
        normalized.append(TestPlanItem(
            item.item_id, item.script_id, item.script_revision,
            item.environment_id, item.environment_revision,
            _json_object(item.default_parameters, "default_parameters"),
            tuple(normalized_rows),
            item.execution_policy,
        ))
    if total_rows > MAX_PLAN_ROWS:
        raise ValueError(f"at most {MAX_PLAN_ROWS} data rows are allowed per test plan")
    return tuple(normalized)


def normalize_plan_text(name: str, description: str) -> tuple[str, str]:
    normalized = name.strip()
    if not normalized or len(normalized) > 255:
        raise ValueError("test plan name must contain 1 to 255 characters")
    if len(description) > 100_000:
        raise ValueError("test plan description cannot exceed 100000 characters")
    return normalized, description


class TestPlanRepository:
    def __init__(
        self, scripts: AutomationRevisionReader, environments: EnvironmentRevisionReader,
        runs: RunRepository,
    ) -> None:
        self._scripts = scripts
        self._environments = environments
        self._runs = runs
        self._lock = RLock()
        self._plans: dict[UUID, TestPlanRecord] = {}
        self._revisions: dict[UUID, list[TestPlanRecord]] = {}
        self._batches: dict[UUID, ExecutionBatchRecord] = {}
        self._specs_by_run: dict[UUID, RunSpecRecord] = {}
        self._idempotency: dict[tuple[UUID, UUID, str], tuple[UUID, int, UUID]] = {}

    def create_plan(
        self, tenant_id: UUID, project_id: UUID, name: str, description: str,
        items: Sequence[TestPlanItem], max_parallel: int = 10,
    ) -> TestPlanRecord:
        name, description = normalize_plan_text(name, description)
        normalized = normalize_plan_items(items)
        max_parallel = self._max_parallel(max_parallel)
        self._validate_references(project_id, normalized)
        now = datetime.now(timezone.utc)
        record = TestPlanRecord(
            uuid4(), tenant_id, project_id, name, description, normalized, max_parallel,
            TestPlanStatus.DRAFT, 1, 0, now, now,
        )
        with self._lock:
            self._plans[record.id] = record
            self._revisions[record.id] = [record]
        return deepcopy(record)

    def get_plan(self, plan_id: UUID) -> TestPlanRecord:
        with self._lock:
            try:
                return deepcopy(self._plans[plan_id])
            except KeyError:
                raise TestPlanNotFound(f"test plan {plan_id} was not found") from None

    def list_plans(self, project_id: UUID) -> list[TestPlanRecord]:
        with self._lock:
            records = [item for item in self._plans.values() if item.project_id == project_id]
            return deepcopy(sorted(records, key=lambda item: (item.created_at, item.id)))

    def update_plan(
        self, plan_id: UUID, *, expected_version: int, name: str,
        description: str, items: Sequence[TestPlanItem], status: TestPlanStatus,
        max_parallel: int = 10,
    ) -> TestPlanRecord:
        name, description = normalize_plan_text(name, description)
        normalized = normalize_plan_items(items)
        max_parallel = self._max_parallel(max_parallel)
        with self._lock:
            current = self.get_plan(plan_id)
            if current.state_version != expected_version:
                raise TestPlanVersionConflict(
                    f"state version mismatch: expected {expected_version}, current {current.state_version}"
                )
            self._validate_references(current.project_id, normalized)
            updated = current.update(
                name=name, description=description, items=normalized,
                status=status, max_parallel=max_parallel,
            )
            self._plans[plan_id] = updated
            self._revisions[plan_id].append(updated)
            return deepcopy(updated)

    def list_revisions(self, plan_id: UUID) -> list[TestPlanRecord]:
        with self._lock:
            self.get_plan(plan_id)
            return deepcopy(self._revisions[plan_id])

    def get_revision(self, plan_id: UUID, revision: int) -> TestPlanRecord:
        with self._lock:
            self.get_plan(plan_id)
            revisions = self._revisions[plan_id]
            if revision < 1 or revision > len(revisions):
                raise TestPlanNotFound(
                    f"test plan {plan_id} revision {revision} was not found"
                )
            return deepcopy(revisions[revision - 1])

    def execute(
        self, plan_id: UUID, idempotency_key: str,
        source_override: tuple[str, str] | None = None,
    ) -> tuple[ExecutionBatchRecord, bool]:
        key = idempotency_key.strip()
        if not key or len(key) > 255:
            raise ValueError("Idempotency-Key must contain 1 to 255 characters")
        with self._lock:
            plan = self.get_plan(plan_id)
            scope = (plan.tenant_id, plan.project_id, key)
            existing = self._idempotency.get(scope)
            if existing is not None:
                saved_plan, saved_revision, batch_id = existing
                if (saved_plan, saved_revision) != (plan.id, plan.revision):
                    raise TestPlanConflict("idempotency key was used for another plan revision")
                return deepcopy(self._batches[batch_id]), True
            if plan.status is not TestPlanStatus.ACTIVE:
                raise TestPlanConflict("only an active test plan revision can be executed")
            now = datetime.now(timezone.utc)
            batch_id = uuid4()
            runs, specs = self._expand(plan, batch_id, now, source_override)
            batch = ExecutionBatchRecord(
                batch_id, plan.tenant_id, plan.project_id, plan.id, plan.revision,
                key, plan.max_parallel, tuple(run.id for run in runs), now,
            )
            with self._runs._lock:
                for run in runs:
                    self._runs._runs[run.id] = run
                self._batches[batch.id] = batch
                self._specs_by_run.update({item.run_id: item for item in specs})
                self._idempotency[scope] = (plan.id, plan.revision, batch.id)
            return deepcopy(batch), False

    def get_execution_batch(self, batch_id: UUID) -> ExecutionBatchRecord:
        with self._lock:
            try:
                return deepcopy(self._batches[batch_id])
            except KeyError:
                raise TestPlanNotFound(f"execution batch {batch_id} was not found") from None

    def claim_available_runs(self, batch_id: UUID) -> tuple[UUID, ...]:
        occupied_states = {
            RunState.QUEUED, RunState.DISPATCHING, RunState.RUNNING,
            RunState.CANCELLING, RunState.RETRY_WAIT,
        }
        with self._lock, self._runs._lock:
            try:
                batch = self._batches[batch_id]
            except KeyError:
                raise TestPlanNotFound(f"execution batch {batch_id} was not found") from None
            records = [self._runs._get_locked(run_id) for run_id in batch.run_ids]
            available = max(
                0, batch.max_parallel
                - sum(record.state in occupied_states for record in records),
            )
            claimed: list[UUID] = []
            for record in records:
                if available == 0:
                    break
                if record.state is not RunState.CREATED:
                    continue
                self._runs._runs[record.id] = record.with_state(RunState.QUEUED)
                claimed.append(record.id)
                available -= 1
            return tuple(claimed)

    def get_run_spec(self, run_id: UUID) -> RunSpecRecord:
        with self._lock:
            try:
                return deepcopy(self._specs_by_run[run_id])
            except KeyError:
                raise RunSpecNotFound(f"run spec for run {run_id} was not found") from None

    def _validate_references(self, project_id: UUID, items: Sequence[TestPlanItem]) -> None:
        for item in items:
            script = self._scripts.get_revision(item.script_id, item.script_revision)
            if script.project_id != project_id:
                raise TestPlanConflict("automation script belongs to a different project")
            if script.status is not AutomationScriptStatus.ACTIVE:
                raise TestPlanConflict("automation script revision must be active")
            if script.engine is not Engine.HTTP and (
                item.execution_policy.before_steps or item.execution_policy.after_steps
                or item.execution_policy.assertions or item.execution_policy.extractions
            ):
                raise TestPlanConflict("HTTP steps, assertions, and extractions require an HTTP script")
            if item.environment_id is not None and item.environment_revision is not None:
                environment = self._environments.get_revision(
                    item.environment_id, item.environment_revision
                )
                if environment.project_id != project_id:
                    raise TestPlanConflict("environment belongs to a different project")
                if environment.status is not EnvironmentStatus.ACTIVE:
                    raise TestPlanConflict("environment revision must be active")

    @staticmethod
    def _max_parallel(value: int) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value < 1 or value > 100:
            raise ValueError("max_parallel must be an integer from 1 to 100")
        return value

    def _expand(
        self, plan: TestPlanRecord, batch_id: UUID, now: datetime,
        source_override: tuple[str, str] | None = None,
    ) -> tuple[list[RunRecord], list[RunSpecRecord]]:
        runs: list[RunRecord] = []
        specs: list[RunSpecRecord] = []
        for item in plan.items:
            script = self._scripts.get_revision(item.script_id, item.script_revision)
            variables: dict[str, str] = {}
            common: dict[str, Any] = {}
            secrets: list[SecretReference] = []
            if item.environment_id is not None and item.environment_revision is not None:
                environment = self._environments.get_revision(
                    item.environment_id, item.environment_revision
                )
                for value in environment.values:
                    if value.secret:
                        if value.secret_ref is None:
                            raise TestPlanConflict("environment secret reference is invalid")
                        secrets.append(SecretReference(
                            category=value.category.value, name=value.name,
                            secret_ref=value.secret_ref,
                        ))
                    elif value.category is ConfigurationCategory.ENVIRONMENT_VARIABLE:
                        variables[value.name] = value.value
                    else:
                        common[value.name] = deepcopy(value.value)
            for row in item.data_rows:
                parameters = {**deepcopy(common), **deepcopy(item.default_parameters), **deepcopy(row.values)}
                parameters = _json_object(parameters, "final parameters")
                run_id = uuid4()
                run = RunRecord(
                    run_id, script.engine, deepcopy(parameters), RunState.CREATED, now, now
                )
                source_ref, content_digest = source_override or (
                    script.source_ref, script.content_digest
                )
                spec = RunSpecRecord(
                    uuid4(), run_id, batch_id, plan.tenant_id, plan.project_id, plan.id,
                    plan.revision, item.item_id, row.row_key, script.engine,
                    script.id, script.revision, source_ref,
                    content_digest, script.entrypoint, script.timeout_seconds,
                    item.environment_id, item.environment_revision, deepcopy(variables),
                    deepcopy(parameters), tuple(secrets), item.execution_policy, now,
                )
                runs.append(run)
                specs.append(spec)
        return runs, specs


class RunSpecTaskMaterializer:
    def __init__(self, repository: RunSpecReader) -> None:
        self._repository = repository

    def materialize(self, run_id: UUID, attempt_id: UUID) -> TaskEnvelope:
        spec = self._repository.get_run_spec(run_id)
        created_at = datetime.now(timezone.utc)
        return TaskEnvelope(
            run_id=run_id, attempt_id=attempt_id,
            task_id=uuid5(NAMESPACE_URL, f"{spec.id}:{attempt_id}"),
            tenant_id=spec.tenant_id, project_id=spec.project_id,
            idempotency_key=f"run-spec:{spec.id}:attempt:{attempt_id}",
            engine=spec.engine, created_at=created_at,
            deadline=created_at + timedelta(seconds=spec.timeout_seconds),
            entrypoint=spec.entrypoint, parameters=spec.parameters,
            environment_id=spec.environment_id,
            environment_revision=spec.environment_revision,
            secret_references=spec.secret_references,
            execution_policy=spec.execution_policy,
            run_spec_id=spec.id, source_ref=spec.source_ref,
            content_digest=spec.content_digest,
            environment_variables=spec.environment_variables,
        )
