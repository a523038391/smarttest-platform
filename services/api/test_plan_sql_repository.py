from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Sequence
from uuid import UUID, uuid4

from packages.protocol import Engine, ExecutionPolicy, SecretReference
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from .automation_domain import AutomationScriptStatus
from .automation_models import AutomationScriptModel, AutomationScriptRevisionModel
from .domain import RunState
from .environment_domain import ConfigurationCategory, EnvironmentStatus
from .environment_models import EnvironmentModel, EnvironmentRevisionModel, EnvironmentValueModel
from .models import RunModel
from .repository import RunRepository
from .test_plan_domain import (
    ExecutionBatchRecord, RunSpecRecord, TestPlanDataRow, TestPlanItem,
    TestPlanRecord, TestPlanStatus,
)
from .test_plan_models import (
    ExecutionBatchModel, RunSpecModel, TestPlanModel, TestPlanRevisionModel,
)
from .test_plan_repository import (
    RunSpecNotFound, TestPlanConflict, TestPlanNotFound,
    TestPlanVersionConflict, _json_object, normalize_plan_items,
    normalize_plan_text,
)


class SqlTestPlanRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def create_plan(
        self, tenant_id: UUID, project_id: UUID, name: str, description: str,
        items: Sequence[TestPlanItem], max_parallel: int = 10,
    ) -> TestPlanRecord:
        name, description = normalize_plan_text(name, description)
        normalized = normalize_plan_items(items)
        max_parallel = self._max_parallel(max_parallel)
        now = datetime.now(timezone.utc)
        record = TestPlanRecord(
            uuid4(), tenant_id, project_id, name, description, normalized, max_parallel,
            TestPlanStatus.DRAFT, 1, 0, now, now,
        )
        with self._session_factory() as session, session.begin():
            self._validate_references(session, project_id, normalized)
            definition = self._definition(normalized)
            session.add(TestPlanModel(
                id=str(record.id), tenant_id=str(tenant_id), project_id=str(project_id),
                name=name, description=description, definition=definition,
                max_parallel=max_parallel,
                status=record.status.value, current_revision=1, state_version=0,
                created_at=now, updated_at=now,
            ))
            session.add(TestPlanRevisionModel(
                plan_id=str(record.id), revision=1, name=name,
                description=description, definition=definition,
                max_parallel=max_parallel,
                status=record.status.value, created_at=now,
            ))
        return record

    def get_plan(self, plan_id: UUID) -> TestPlanRecord:
        with self._session_factory() as session:
            model = session.get(TestPlanModel, str(plan_id))
            if model is None:
                raise self._not_found(plan_id)
            return self._record(model)

    def list_plans(self, project_id: UUID) -> list[TestPlanRecord]:
        with self._session_factory() as session:
            query = select(TestPlanModel).where(
                TestPlanModel.project_id == str(project_id)
            ).order_by(TestPlanModel.created_at, TestPlanModel.id)
            return [self._record(item) for item in session.scalars(query)]

    def update_plan(
        self, plan_id: UUID, *, expected_version: int, name: str,
        description: str, items: Sequence[TestPlanItem], status: TestPlanStatus,
        max_parallel: int = 10,
    ) -> TestPlanRecord:
        name, description = normalize_plan_text(name, description)
        normalized = normalize_plan_items(items)
        max_parallel = self._max_parallel(max_parallel)
        with self._session_factory() as session, session.begin():
            model = session.scalar(select(TestPlanModel).where(
                TestPlanModel.id == str(plan_id)
            ).with_for_update())
            if model is None:
                raise self._not_found(plan_id)
            current = self._record(model)
            if current.state_version != expected_version:
                raise TestPlanVersionConflict(
                    f"state version mismatch: expected {expected_version}, current {current.state_version}"
                )
            self._validate_references(session, current.project_id, normalized)
            updated = current.update(
                name=name, description=description, items=normalized,
                status=status, max_parallel=max_parallel,
            )
            definition = self._definition(normalized)
            model.name, model.description, model.definition = name, description, definition
            model.status = status.value
            model.max_parallel = max_parallel
            model.current_revision = updated.revision
            model.state_version = updated.state_version
            model.updated_at = updated.updated_at
            session.add(TestPlanRevisionModel(
                plan_id=model.id, revision=updated.revision, name=name,
                description=description, definition=definition,
                max_parallel=max_parallel,
                status=status.value, created_at=updated.updated_at,
            ))
            return updated

    def list_revisions(self, plan_id: UUID) -> list[TestPlanRecord]:
        with self._session_factory() as session:
            parent = session.get(TestPlanModel, str(plan_id))
            if parent is None:
                raise self._not_found(plan_id)
            query = select(TestPlanRevisionModel).where(
                TestPlanRevisionModel.plan_id == str(plan_id)
            ).order_by(TestPlanRevisionModel.revision)
            return [self._revision_record(parent, item) for item in session.scalars(query)]

    def get_revision(self, plan_id: UUID, revision: int) -> TestPlanRecord:
        with self._session_factory() as session:
            parent = session.get(TestPlanModel, str(plan_id))
            if parent is None:
                raise self._not_found(plan_id)
            model = session.get(TestPlanRevisionModel, (str(plan_id), revision))
            if model is None:
                raise TestPlanNotFound(
                    f"test plan {plan_id} revision {revision} was not found"
                )
            return self._revision_record(parent, model)

    def execute(
        self, plan_id: UUID, idempotency_key: str,
        source_override: tuple[str, str] | None = None,
    ) -> tuple[ExecutionBatchRecord, bool]:
        key = idempotency_key.strip()
        if not key or len(key) > 255:
            raise ValueError("Idempotency-Key must contain 1 to 255 characters")
        session = self._session_factory()
        try:
            try:
                with session.begin():
                    plan_model = session.scalar(select(TestPlanModel).where(
                        TestPlanModel.id == str(plan_id)
                    ).with_for_update())
                    if plan_model is None:
                        raise self._not_found(plan_id)
                    existing = self._batch_by_key(
                        session, plan_model.tenant_id, plan_model.project_id, key
                    )
                    if existing is not None:
                        return self._resolve_batch(session, existing, plan_model), True
                    plan = self._record(plan_model)
                    if plan.status is not TestPlanStatus.ACTIVE:
                        raise TestPlanConflict("only an active test plan revision can be executed")
                    batch = ExecutionBatchModel(
                        id=str(uuid4()), tenant_id=plan_model.tenant_id,
                        project_id=plan_model.project_id, plan_id=plan_model.id,
                        plan_revision=plan_model.current_revision,
                        idempotency_key=key, max_parallel=plan.max_parallel,
                        created_at=datetime.now(timezone.utc),
                    )
                    session.add(batch)
                    self._expand(session, plan, batch, source_override)
                    session.flush()
                    return self._batch_record(session, batch), False
            except IntegrityError as conflict:
                with session.begin():
                    plan_model = session.get(TestPlanModel, str(plan_id))
                    if plan_model is None:
                        raise self._not_found(plan_id)
                    existing = self._batch_by_key(
                        session, plan_model.tenant_id, plan_model.project_id, key
                    )
                    if existing is None:
                        raise conflict
                    return self._resolve_batch(session, existing, plan_model), True
        finally:
            session.close()

    def get_execution_batch(self, batch_id: UUID) -> ExecutionBatchRecord:
        with self._session_factory() as session:
            model = session.get(ExecutionBatchModel, str(batch_id))
            if model is None:
                raise TestPlanNotFound(f"execution batch {batch_id} was not found")
            return self._batch_record(session, model)

    def claim_available_runs(self, batch_id: UUID) -> tuple[UUID, ...]:
        occupied_states = {
            RunState.QUEUED, RunState.DISPATCHING, RunState.RUNNING,
            RunState.CANCELLING, RunState.RETRY_WAIT,
        }
        with self._session_factory() as session, session.begin():
            batch = session.scalar(select(ExecutionBatchModel).where(
                ExecutionBatchModel.id == str(batch_id)
            ).with_for_update())
            if batch is None:
                raise TestPlanNotFound(f"execution batch {batch_id} was not found")
            query = (
                select(RunModel)
                .join(RunSpecModel, RunSpecModel.run_id == RunModel.id)
                .where(RunSpecModel.batch_id == batch.id)
                .order_by(RunSpecModel.position)
                .with_for_update()
            )
            records = list(session.scalars(query))
            available = max(
                0, batch.max_parallel
                - sum(RunState(record.state) in occupied_states for record in records),
            )
            claimed: list[UUID] = []
            now = datetime.now(timezone.utc)
            for record in records:
                if available == 0:
                    break
                if RunState(record.state) is not RunState.CREATED:
                    continue
                record.state = RunState.QUEUED.value
                record.updated_at = now
                record.state_version += 1
                claimed.append(UUID(record.id))
                available -= 1
            session.flush()
            return tuple(claimed)

    def get_run_spec(self, run_id: UUID) -> RunSpecRecord:
        with self._session_factory() as session:
            model = session.scalar(select(RunSpecModel).where(
                RunSpecModel.run_id == str(run_id)
            ))
            if model is None:
                raise RunSpecNotFound(f"run spec for run {run_id} was not found")
            return self._spec_record(model)

    def _expand(
        self, session: Session, plan: TestPlanRecord, batch: ExecutionBatchModel,
        source_override: tuple[str, str] | None = None,
    ) -> None:
        position = 0
        for item in plan.items:
            script_parent, script = self._script_revision(
                session, plan.project_id, item.script_id, item.script_revision
            )
            variables, common, secrets = self._environment_snapshot(session, plan.project_id, item)
            for row in item.data_rows:
                parameters = _json_object(
                    {**deepcopy(common), **deepcopy(item.default_parameters), **deepcopy(row.values)},
                    "final parameters",
                )
                now, run_id, spec_id = datetime.now(timezone.utc), uuid4(), uuid4()
                session.add(RunModel(
                    id=str(run_id), engine=script_parent.engine, parameters=parameters,
                    idempotency_key=None,
                    fingerprint=RunRepository._fingerprint(Engine(script_parent.engine), parameters),
                    state=RunState.CREATED.value, created_at=now, updated_at=now,
                    state_version=0,
                ))
                source_ref, content_digest = source_override or (
                    script.source_ref, script.content_digest
                )
                session.add(RunSpecModel(
                    id=str(spec_id), run_id=str(run_id), batch_id=batch.id,
                    position=position, tenant_id=str(plan.tenant_id),
                    project_id=str(plan.project_id), plan_id=str(plan.id),
                    plan_revision=plan.revision, item_id=str(item.item_id),
                    row_key=row.row_key, engine=script_parent.engine,
                    script_id=str(item.script_id), script_revision=item.script_revision,
                    source_ref=source_ref, content_digest=content_digest,
                    entrypoint=script.entrypoint, timeout_seconds=script.timeout_seconds,
                    environment_id=str(item.environment_id) if item.environment_id else None,
                    environment_revision=item.environment_revision,
                    environment_variables=variables, parameters=parameters,
                    secret_references=[item.model_dump(mode="json") for item in secrets],
                    execution_policy=item.execution_policy.model_dump(mode="json"),
                    created_at=now,
                ))
                position += 1

    def _validate_references(
        self, session: Session, project_id: UUID, items: Sequence[TestPlanItem]
    ) -> None:
        for item in items:
            script, _ = self._script_revision(
                session, project_id, item.script_id, item.script_revision
            )
            if Engine(script.engine) is not Engine.HTTP and (
                item.execution_policy.before_steps or item.execution_policy.after_steps
                or item.execution_policy.assertions or item.execution_policy.extractions
            ):
                raise TestPlanConflict(
                    "HTTP steps, assertions, and extractions require an HTTP script"
                )
            if item.environment_id is not None:
                assert item.environment_revision is not None
                self._environment_revision(
                    session, project_id, item.environment_id,
                    item.environment_revision,
                )

    @staticmethod
    def _script_revision(
        session: Session, project_id: UUID, script_id: UUID, revision: int,
    ) -> tuple[AutomationScriptModel, AutomationScriptRevisionModel]:
        parent = session.get(AutomationScriptModel, str(script_id))
        script = session.get(AutomationScriptRevisionModel, (str(script_id), revision))
        if parent is None or script is None:
            raise TestPlanConflict("automation script revision was not found")
        if parent.project_id != str(project_id):
            raise TestPlanConflict("automation script belongs to a different project")
        if script.status != AutomationScriptStatus.ACTIVE.value:
            raise TestPlanConflict("automation script revision must be active")
        return parent, script

    @staticmethod
    def _environment_revision(
        session: Session, project_id: UUID, environment_id: UUID, revision: int,
    ) -> EnvironmentRevisionModel:
        parent = session.get(EnvironmentModel, str(environment_id))
        environment = session.get(EnvironmentRevisionModel, (str(environment_id), revision))
        if parent is None or environment is None:
            raise TestPlanConflict("environment revision was not found")
        if parent.project_id != str(project_id):
            raise TestPlanConflict("environment belongs to a different project")
        if environment.status != EnvironmentStatus.ACTIVE.value:
            raise TestPlanConflict("environment revision must be active")
        return environment

    def _environment_snapshot(
        self, session: Session, project_id: UUID, item: TestPlanItem,
    ) -> tuple[dict[str, str], dict[str, Any], tuple[SecretReference, ...]]:
        if item.environment_id is None or item.environment_revision is None:
            return {}, {}, ()
        self._environment_revision(
            session, project_id, item.environment_id, item.environment_revision
        )
        query = select(EnvironmentValueModel).where(
            EnvironmentValueModel.environment_id == str(item.environment_id),
            EnvironmentValueModel.revision == item.environment_revision,
        ).order_by(EnvironmentValueModel.category.desc(), EnvironmentValueModel.position)
        variables: dict[str, str] = {}
        common: dict[str, Any] = {}
        secrets: list[SecretReference] = []
        for value in session.scalars(query):
            if value.is_secret:
                if value.secret_ref is None:
                    raise TestPlanConflict("environment secret reference is invalid")
                secrets.append(SecretReference(
                    category=value.category, name=value.name,
                    secret_ref=UUID(value.secret_ref),
                ))
            elif value.category == ConfigurationCategory.ENVIRONMENT_VARIABLE.value:
                variables[value.name] = value.public_value
            else:
                common[value.name] = deepcopy(value.public_value)
        return variables, common, tuple(secrets)

    @staticmethod
    def _definition(items: Sequence[TestPlanItem]) -> list[dict[str, Any]]:
        return [{
            "item_id": str(item.item_id), "script_id": str(item.script_id),
            "script_revision": item.script_revision,
            "environment_id": str(item.environment_id) if item.environment_id else None,
            "environment_revision": item.environment_revision,
            "default_parameters": deepcopy(item.default_parameters),
            "data_rows": [
                {"row_key": row.row_key, "values": deepcopy(row.values)}
                for row in item.data_rows
            ],
            "execution_policy": item.execution_policy.model_dump(mode="json"),
        } for item in items]

    @staticmethod
    def _items(definition: list[dict[str, Any]]) -> tuple[TestPlanItem, ...]:
        return tuple(TestPlanItem(
            UUID(item["item_id"]), UUID(item["script_id"]), item["script_revision"],
            UUID(item["environment_id"]) if item.get("environment_id") else None,
            item.get("environment_revision"), deepcopy(item["default_parameters"]),
            tuple(TestPlanDataRow(row["row_key"], deepcopy(row["values"]))
                  for row in item["data_rows"]),
            ExecutionPolicy.model_validate(item.get("execution_policy", {})),
        ) for item in definition)

    def _record(self, model: TestPlanModel) -> TestPlanRecord:
        return TestPlanRecord(
            UUID(model.id), UUID(model.tenant_id), UUID(model.project_id), model.name,
            model.description, self._items(model.definition), model.max_parallel,
            TestPlanStatus(model.status),
            model.current_revision, model.state_version, self._utc(model.created_at),
            self._utc(model.updated_at),
        )

    def _revision_record(
        self, parent: TestPlanModel, model: TestPlanRevisionModel,
    ) -> TestPlanRecord:
        return TestPlanRecord(
            UUID(parent.id), UUID(parent.tenant_id), UUID(parent.project_id), model.name,
            model.description, self._items(model.definition), model.max_parallel,
            TestPlanStatus(model.status),
            model.revision, model.revision - 1, self._utc(parent.created_at),
            self._utc(model.created_at),
        )

    @staticmethod
    def _batch_by_key(
        session: Session, tenant_id: str, project_id: str, key: str,
    ) -> ExecutionBatchModel | None:
        return session.scalar(select(ExecutionBatchModel).where(
            ExecutionBatchModel.tenant_id == tenant_id,
            ExecutionBatchModel.project_id == project_id,
            ExecutionBatchModel.idempotency_key == key,
        ))

    def _resolve_batch(
        self, session: Session, batch: ExecutionBatchModel, plan: TestPlanModel,
    ) -> ExecutionBatchRecord:
        if (batch.plan_id, batch.plan_revision) != (plan.id, plan.current_revision):
            raise TestPlanConflict("idempotency key was used for another plan revision")
        return self._batch_record(session, batch)

    def _batch_record(
        self, session: Session, model: ExecutionBatchModel,
    ) -> ExecutionBatchRecord:
        query = select(RunSpecModel.run_id).where(
            RunSpecModel.batch_id == model.id
        ).order_by(RunSpecModel.position)
        return ExecutionBatchRecord(
            UUID(model.id), UUID(model.tenant_id), UUID(model.project_id),
            UUID(model.plan_id), model.plan_revision, model.idempotency_key,
            model.max_parallel,
            tuple(UUID(item) for item in session.scalars(query)),
            self._utc(model.created_at),
        )

    def _spec_record(self, model: RunSpecModel) -> RunSpecRecord:
        return RunSpecRecord(
            UUID(model.id), UUID(model.run_id), UUID(model.batch_id), UUID(model.tenant_id),
            UUID(model.project_id), UUID(model.plan_id), model.plan_revision,
            UUID(model.item_id), model.row_key, Engine(model.engine),
            UUID(model.script_id), model.script_revision, model.source_ref,
            model.content_digest, model.entrypoint, model.timeout_seconds,
            UUID(model.environment_id) if model.environment_id else None,
            model.environment_revision, deepcopy(model.environment_variables),
            deepcopy(model.parameters),
            tuple(SecretReference.model_validate(item) for item in model.secret_references),
            ExecutionPolicy.model_validate(model.execution_policy),
            self._utc(model.created_at),
        )

    @staticmethod
    def _not_found(plan_id: UUID) -> TestPlanNotFound:
        return TestPlanNotFound(f"test plan {plan_id} was not found")

    @staticmethod
    def _utc(value: datetime) -> datetime:
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)

    @staticmethod
    def _max_parallel(value: int) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value < 1 or value > 100:
            raise ValueError("max_parallel must be an integer from 1 to 100")
        return value