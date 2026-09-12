from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from packages.protocol import Engine, SecretReference, TaskEnvelope
from services.api.database import Base, create_database_engine, create_session_factory
from services.api.domain import AttemptState
from services.api.environment_crypto import SecretEncryptor
from services.api.environment_domain import ConfigurationValueInput, EnvironmentStatus
from services.api.environment_repository import ConfigurationResolver
from services.api.environment_sql_repository import SqlEnvironmentRepository
from services.api.secret_grant_models import SecretCapabilityGrantModel
from services.api.secret_grant_sql_repository import SqlSecretGrantRepository
from services.api.sql_repository import SqlRunRepository
from services.secret_broker import CapabilityRejected, SecretBroker


def _setup(tmp_path):
    engine = create_database_engine(
        f"sqlite+pysqlite:///{(tmp_path / 'secret-grants.sqlite3').as_posix()}"
    )
    Base.metadata.create_all(engine)
    sessions = create_session_factory(engine)
    encryptor = SecretEncryptor(b"q" * 32, "sql-broker-test")
    environments = SqlEnvironmentRepository(sessions, encryptor)
    project_id = uuid4()
    created = environments.create_environment(
        project_id, "sql-broker",
        [ConfigurationValueInput("TOKEN", "value-under-test", secret=True)], [],
    )
    active = environments.update_environment(
        created.id, expected_version=0, name=created.name,
        status=EnvironmentStatus.ACTIVE,
        environment_variables=[ConfigurationValueInput(
            "TOKEN", secret=True, secret_ref=created.values[0].secret_ref
        )], common_parameters=[],
    )
    runs = SqlRunRepository(sessions)
    run = runs.create(Engine.PYTEST, {})
    attempt = runs.create_attempt(run.id)
    now = datetime.now(timezone.utc)
    runs.claim_attempt(attempt.id, "runner-a", 120, now)
    runs.transition_attempt(attempt.id, AttemptState.PREPARING)
    runs.transition_attempt(attempt.id, AttemptState.RUNNING)
    task = TaskEnvelope(
        run_id=run.id, attempt_id=attempt.id, task_id=uuid4(),
        tenant_id=uuid4(), project_id=project_id, engine=Engine.PYTEST,
        idempotency_key="sql-broker", created_at=now,
        deadline=now + timedelta(seconds=90), entrypoint="test.py",
        environment_id=active.id, environment_revision=active.revision,
        secret_references=(SecretReference(
            category="environment_variable", name="TOKEN",
            secret_ref=active.values[0].secret_ref,
        ),),
    )
    broker = SecretBroker(
        SqlSecretGrantRepository(sessions),
        ConfigurationResolver(environments, encryptor), clock=lambda: now,
    )
    return engine, sessions, runs, broker, task


def test_sql_stores_digest_and_manifest_but_no_token_or_plaintext(tmp_path) -> None:
    engine, sessions, _, broker, task = _setup(tmp_path)
    try:
        capability = broker.issue(task, "runner-a")
        with sessions() as session:
            stored = session.query(SecretCapabilityGrantModel).one()
            persisted = "|".join((
                stored.token_digest, str(stored.reference_manifest),
                stored.task_id, stored.runner_id,
            ))
        assert stored.token_digest == broker.digest(capability.token)
        assert capability.token not in persisted
        assert "value-under-test" not in persisted
    finally:
        engine.dispose()


def test_sql_grant_is_one_time_and_checks_attempt_owner_and_state(tmp_path) -> None:
    engine, _, runs, broker, task = _setup(tmp_path)
    try:
        with pytest.raises(CapabilityRejected, match="secret capability was rejected"):
            broker.issue(task, "runner-b")

        capability = broker.issue(task, "runner-a")
        assert broker.redeem(capability, task, "runner-a")[0].name == "TOKEN"
        with pytest.raises(CapabilityRejected, match="secret capability was rejected"):
            broker.redeem(capability, task, "runner-a")

        runs.transition_attempt(task.attempt_id, AttemptState.COLLECTING)
        with pytest.raises(CapabilityRejected, match="secret capability was rejected"):
            broker.issue(task, "runner-a")
    finally:
        engine.dispose()


def test_sql_revoke_is_idempotent_after_consumption(tmp_path) -> None:
    engine, _, _, broker, task = _setup(tmp_path)
    try:
        capability = broker.issue(task, "runner-a")
        broker.redeem(capability, task, "runner-a")
        broker.revoke(capability)
        broker.revoke(capability)
    finally:
        engine.dispose()