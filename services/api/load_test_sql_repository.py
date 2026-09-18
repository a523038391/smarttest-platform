from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Sequence
from uuid import UUID, uuid4

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from .load_test_domain import (
    LoadTestMode, LoadTestRecord, LoadTestRunRecord, LoadTestRunStatus,
    LoadTestTrafficMode, empty_metrics,
)
from .load_test_models import LoadTestModel, LoadTestRunModel
from .load_test_repository import (
    LoadTestActiveRunConflict, LoadTestNameConflict,
    LoadTestNotFound, LoadTestRunNotFound, LoadTestVersionConflict,
    normalize_name, normalize_scenario_settings, normalize_targets, validate_settings,
)


class SqlLoadTestRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

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
        now, load_test_id = datetime.now(timezone.utc), uuid4()
        model = LoadTestModel(
            id=str(load_test_id), project_id=str(project_id),
            environment_id=str(environment_id) if environment_id else None,
            name=normalized_name,
            description=description, targets=deepcopy(normalized_targets),
            mode=mode.value, request_count=request_count,
            duration_seconds=duration_seconds, concurrency=concurrency,
            interval_ms=interval_ms, timeout_seconds=timeout_seconds,
            traffic_mode=traffic_mode.value,
            initial_variables=deepcopy(initial_variables), stop_on_failure=stop_on_failure,
            state_version=0, created_at=now, updated_at=now,
        )
        try:
            with self._session_factory() as session, session.begin():
                session.add(model)
        except IntegrityError:
            raise LoadTestNameConflict("a load test with this project and name exists") from None
        return self._definition(model)

    def get_load_test(self, load_test_id: UUID) -> LoadTestRecord:
        with self._session_factory() as session:
            model = session.get(LoadTestModel, str(load_test_id))
            if model is None:
                raise self._definition_not_found(load_test_id)
            return self._definition(model)

    def list_load_tests(self, project_id: UUID) -> list[LoadTestRecord]:
        with self._session_factory() as session:
            query = select(LoadTestModel).where(
                LoadTestModel.project_id == str(project_id)
            ).order_by(LoadTestModel.created_at)
            return [self._definition(model) for model in session.scalars(query)]

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
        try:
            with self._session_factory() as session, session.begin():
                model = session.scalar(select(LoadTestModel).where(
                    LoadTestModel.id == str(load_test_id)
                ).with_for_update())
                if model is None:
                    raise self._definition_not_found(load_test_id)
                if model.state_version != expected_version:
                    raise LoadTestVersionConflict("load test state version mismatch")
                model.name, model.description = normalized_name, description
                model.targets, model.mode = deepcopy(normalized_targets), mode.value
                model.request_count, model.duration_seconds = request_count, duration_seconds
                model.concurrency, model.interval_ms = concurrency, interval_ms
                model.timeout_seconds = timeout_seconds
                model.environment_id = str(environment_id) if environment_id else None
                model.traffic_mode = traffic_mode.value
                model.initial_variables = deepcopy(initial_variables)
                model.stop_on_failure = stop_on_failure
                model.state_version += 1
                model.updated_at = datetime.now(timezone.utc)
                session.flush()
                return self._definition(model)
        except IntegrityError:
            raise LoadTestNameConflict("a load test with this project and name exists") from None

    def delete_load_test(self, load_test_id: UUID) -> None:
        with self._session_factory() as session, session.begin():
            model = session.scalar(select(LoadTestModel).where(
                LoadTestModel.id == str(load_test_id)
            ).with_for_update())
            if model is None:
                raise self._definition_not_found(load_test_id)
            if self._active(session, load_test_id) is not None:
                raise LoadTestActiveRunConflict("load test has an active run")
            session.execute(delete(LoadTestRunModel).where(
                LoadTestRunModel.load_test_id == str(load_test_id)
            ))
            session.delete(model)

    def create_run(self, load_test_id: UUID) -> LoadTestRunRecord:
        with self._session_factory() as session, session.begin():
            definition = session.scalar(select(LoadTestModel).where(
                LoadTestModel.id == str(load_test_id)
            ).with_for_update())
            if definition is None:
                raise self._definition_not_found(load_test_id)
            if self._active(session, load_test_id) is not None:
                raise LoadTestActiveRunConflict("load test already has an active run")
            model = LoadTestRunModel(
                id=str(uuid4()), load_test_id=str(load_test_id),
                status=LoadTestRunStatus.RUNNING.value, mode=definition.mode,
                started_at=datetime.now(timezone.utc), finished_at=None,
                metrics=empty_metrics([target["name"] for target in definition.targets]),
                error_message=None,
            )
            session.add(model)
            session.flush()
            return self._run(model)

    def update_run(self, run_id: UUID, *, status: LoadTestRunStatus,
                   metrics: dict[str, Any], error_message: str | None = None,
                   finished_at: datetime | None = None) -> LoadTestRunRecord:
        with self._session_factory() as session, session.begin():
            model = session.scalar(select(LoadTestRunModel).where(
                LoadTestRunModel.id == str(run_id)
            ).with_for_update())
            if model is None:
                raise self._run_not_found(run_id)
            if model.status != LoadTestRunStatus.RUNNING.value:
                return self._run(model)
            model.status, model.metrics = LoadTestRunStatus(status).value, deepcopy(metrics)
            model.error_message, model.finished_at = error_message, finished_at
            session.flush()
            return self._run(model)

    def recover_running_runs(self, message: str) -> int:
        with self._session_factory() as session, session.begin():
            result = session.execute(update(LoadTestRunModel).where(
                LoadTestRunModel.status == LoadTestRunStatus.RUNNING.value
            ).values(
                status=LoadTestRunStatus.FAILED.value,
                error_message=message,
                finished_at=datetime.now(timezone.utc),
            ))
            return int(result.rowcount or 0)

    def get_run(self, run_id: UUID) -> LoadTestRunRecord:
        with self._session_factory() as session:
            model = session.get(LoadTestRunModel, str(run_id))
            if model is None:
                raise self._run_not_found(run_id)
            return self._run(model)

    def list_runs(self, load_test_id: UUID) -> list[LoadTestRunRecord]:
        with self._session_factory() as session:
            if session.get(LoadTestModel, str(load_test_id)) is None:
                raise self._definition_not_found(load_test_id)
            query = select(LoadTestRunModel).where(
                LoadTestRunModel.load_test_id == str(load_test_id)
            ).order_by(LoadTestRunModel.started_at.desc())
            return [self._run(model) for model in session.scalars(query)]

    @staticmethod
    def _active(session: Session, load_test_id: UUID) -> LoadTestRunModel | None:
        return session.scalar(select(LoadTestRunModel).where(
            LoadTestRunModel.load_test_id == str(load_test_id),
            LoadTestRunModel.status == LoadTestRunStatus.RUNNING.value,
        ).limit(1))

    @staticmethod
    def _definition(model: LoadTestModel) -> LoadTestRecord:
        return LoadTestRecord(
            id=UUID(model.id), project_id=UUID(model.project_id), name=model.name,
            description=model.description, targets=deepcopy(model.targets),
            mode=LoadTestMode(model.mode), request_count=model.request_count,
            duration_seconds=model.duration_seconds, concurrency=model.concurrency,
            interval_ms=model.interval_ms, timeout_seconds=model.timeout_seconds,
            state_version=model.state_version,
            created_at=SqlLoadTestRepository._utc(model.created_at),
            updated_at=SqlLoadTestRepository._utc(model.updated_at),
            environment_id=UUID(model.environment_id) if model.environment_id else None,
            traffic_mode=LoadTestTrafficMode(model.traffic_mode),
            initial_variables=deepcopy(model.initial_variables),
            stop_on_failure=model.stop_on_failure,
        )

    @staticmethod
    def _run(model: LoadTestRunModel) -> LoadTestRunRecord:
        return LoadTestRunRecord(
            UUID(model.id), UUID(model.load_test_id), LoadTestRunStatus(model.status),
            LoadTestMode(model.mode), SqlLoadTestRepository._utc(model.started_at),
            SqlLoadTestRepository._utc(model.finished_at) if model.finished_at else None,
            deepcopy(model.metrics), model.error_message,
        )

    @staticmethod
    def _utc(value: datetime) -> datetime:
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)

    @staticmethod
    def _definition_not_found(value: UUID) -> LoadTestNotFound:
        return LoadTestNotFound(f"load test {value} was not found")

    @staticmethod
    def _run_not_found(value: UUID) -> LoadTestRunNotFound:
        return LoadTestRunNotFound(f"load test run {value} was not found")