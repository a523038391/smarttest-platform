from collections.abc import Iterator
from uuid import uuid4

import pytest
from sqlalchemy import Engine, func, select

from packages.protocol import Engine as ExecutionEngine
from services.api.automation_domain import AutomationScriptStatus
from services.api.automation_sql_repository import SqlAutomationRepository
from services.api.database import Base, create_database_engine, create_session_factory
from services.api.environment_crypto import SecretEncryptor
from services.api.environment_domain import EnvironmentStatus
from services.api.environment_sql_repository import SqlEnvironmentRepository
from services.api.models import RunModel
from services.api.test_plan_domain import TestPlanItem as PlanItem, TestPlanStatus as PlanStatus
from services.api.test_plan_models import RunSpecModel
from services.api.test_plan_repository import TestPlanConflict as PlanConflict
from services.api.test_plan_sql_repository import SqlTestPlanRepository


@pytest.fixture
def database(tmp_path) -> Iterator[tuple[Engine, object]]:
    engine = create_database_engine(
        f"sqlite+pysqlite:///{(tmp_path / 'plans.sqlite3').as_posix()}"
    )
    Base.metadata.create_all(engine)
    yield engine, create_session_factory(engine)
    engine.dispose()


def test_sql_plan_execution_is_atomic_persisted_and_idempotent(database) -> None:
    engine, sessions = database
    project_id, tenant_id = uuid4(), uuid4()
    scripts = SqlAutomationRepository(sessions)
    script = scripts.create_script(
        project_id, "api", "", ExecutionEngine.HTTP, "case.json",
        "artifact:case-v1", "d" * 64, 45,
    )
    script = scripts.update_script(
        script.id, expected_version=0, name=script.name, description="",
        entrypoint=script.entrypoint, source_ref=script.source_ref,
        content_digest=script.content_digest, timeout_seconds=45,
        status=AutomationScriptStatus.ACTIVE,
    )
    environments = SqlEnvironmentRepository(
        sessions, SecretEncryptor(b"s" * 32, "key-1")
    )
    environment = environments.create_environment(project_id, "empty", [], [])
    environment = environments.update_environment(
        environment.id, expected_version=0, name=environment.name,
        status=EnvironmentStatus.ACTIVE,
        environment_variables=[], common_parameters=[],
    )
    repository = SqlTestPlanRepository(sessions)
    item = PlanItem(
        uuid4(), script.id, script.revision, environment.id,
        environment.revision, {"suite": "smoke"}, (),
    )
    plan = repository.create_plan(tenant_id, project_id, "nightly", "", [item])
    plan = repository.update_plan(
        plan.id, expected_version=0, name=plan.name, description="",
        items=[item], status=PlanStatus.ACTIVE,
    )

    batch, replayed = repository.execute(plan.id, "sql-key")
    repeated, was_replayed = repository.execute(plan.id, "sql-key")
    spec = repository.get_run_spec(batch.run_ids[0])

    assert not replayed and was_replayed and repeated.id == batch.id
    assert spec.parameters == {"suite": "smoke"}
    assert spec.source_ref == "artifact:case-v1"
    assert repository.get_execution_batch(batch.id).run_ids == batch.run_ids
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(RunModel)) == 1
        assert session.scalar(select(func.count()).select_from(RunSpecModel)) == 1

    changed = repository.update_plan(
        plan.id, expected_version=1, name=plan.name, description="changed",
        items=[item], status=PlanStatus.ACTIVE,
    )
    with pytest.raises(PlanConflict, match="another plan revision"):
        repository.execute(changed.id, "sql-key")