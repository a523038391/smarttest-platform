from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock
from uuid import uuid4

import pytest

from packages.protocol import Engine, SecretReference, TaskEnvelope
from services.api.domain import AttemptState
from services.api.environment_crypto import SecretEncryptor
from services.api.environment_domain import ConfigurationValueInput, EnvironmentStatus
from services.api.environment_repository import ConfigurationResolver, EnvironmentRepository
from services.api.repository import RunRepository
from services.secret_broker import (
    CapabilityRejected,
    InMemorySecretGrantStore,
    SecretBroker,
)


def _setup(*, ttl_seconds: int = 60):
    now = datetime.now(timezone.utc)
    clock = [now]
    encryptor = SecretEncryptor(b"b" * 32, "broker-test")
    environments = EnvironmentRepository(encryptor)
    project_id = uuid4()
    created = environments.create_environment(
        project_id, "broker", [ConfigurationValueInput(
            "TOKEN", "value-under-test", secret=True
        )], [],
    )
    secret = created.values[0]
    active = environments.update_environment(
        created.id, expected_version=0, name=created.name,
        status=EnvironmentStatus.ACTIVE,
        environment_variables=[ConfigurationValueInput(
            "TOKEN", secret=True, secret_ref=secret.secret_ref
        )], common_parameters=[],
    )
    reference = SecretReference(
        category="environment_variable", name="TOKEN",
        secret_ref=active.values[0].secret_ref,
    )
    runs = RunRepository()
    run = runs.create(Engine.PYTEST, {})
    attempt = runs.create_attempt(run.id)
    runs.claim_attempt(attempt.id, "runner-a", 120, now)
    runs.transition_attempt(attempt.id, AttemptState.PREPARING)
    runs.transition_attempt(attempt.id, AttemptState.RUNNING)
    task = TaskEnvelope(
        run_id=run.id, attempt_id=attempt.id, task_id=uuid4(),
        tenant_id=uuid4(), project_id=project_id, engine=Engine.PYTEST,
        idempotency_key="broker-test", created_at=now,
        deadline=now + timedelta(seconds=90), entrypoint="test.py",
        environment_id=active.id, environment_revision=active.revision,
        secret_references=(reference,),
    )
    decrypting = Mock(wraps=encryptor)
    broker = SecretBroker(
        InMemorySecretGrantStore(runs),
        ConfigurationResolver(environments, decrypting),
        ttl_seconds=ttl_seconds, clock=lambda: clock[0],
    )
    return broker, task, decrypting, clock


def test_issue_validates_without_decrypt_and_redeem_is_exact_and_one_time() -> None:
    broker, task, encryptor, _ = _setup()
    capability = broker.issue(task, "runner-a")

    assert encryptor.decrypt.call_count == 0
    assert capability.token not in repr(capability)
    wrong_task = task.model_copy(update={"task_id": uuid4()})
    with pytest.raises(CapabilityRejected, match="secret capability was rejected"):
        broker.redeem(capability, wrong_task, "runner-a")
    assert encryptor.decrypt.call_count == 0

    values = broker.redeem(capability, task, "runner-a")
    assert values[0].value == "value-under-test"
    assert values[0].value not in repr(values[0])
    with pytest.raises(CapabilityRejected, match="secret capability was rejected"):
        broker.redeem(capability, task, "runner-a")


def test_expiry_revoke_and_empty_grants_are_rejected_generically() -> None:
    broker, task, _, clock = _setup(ttl_seconds=1)
    expired = broker.issue(task, "runner-a")
    clock[0] += timedelta(seconds=2)
    with pytest.raises(CapabilityRejected, match="secret capability was rejected"):
        broker.redeem(expired, task, "runner-a")

    broker, task, _, _ = _setup()
    revoked = broker.issue(task, "runner-a")
    broker.revoke(revoked)
    broker.revoke(revoked)
    with pytest.raises(CapabilityRejected, match="secret capability was rejected"):
        broker.redeem(revoked, task, "runner-a")

    broker, task, _, _ = _setup()
    attempt_revoked = broker.issue(task, "runner-a")
    assert broker.revoke_attempt(task.attempt_id) == 1
    with pytest.raises(CapabilityRejected, match="secret capability was rejected"):
        broker.redeem(attempt_revoked, task, "runner-a")
    empty = task.model_copy(update={"secret_references": ()})
    with pytest.raises(CapabilityRejected, match="secret capability was rejected"):
        broker.issue(empty, "runner-a")


def test_revoke_is_idempotent_after_capability_consumption() -> None:
    broker, task, _, _ = _setup()
    capability = broker.issue(task, "runner-a")
    broker.redeem(capability, task, "runner-a")
    broker.revoke(capability)
    broker.revoke(capability)


def test_capability_is_consumed_before_secret_resolution() -> None:
    broker, task, encryptor, _ = _setup()
    capability = broker.issue(task, "runner-a")
    encryptor.decrypt.side_effect = RuntimeError("resolution failed")

    with pytest.raises(CapabilityRejected, match="secret capability was rejected"):
        broker.redeem(capability, task, "runner-a")
    assert encryptor.decrypt.call_count == 1
    encryptor.decrypt.side_effect = None
    with pytest.raises(CapabilityRejected, match="secret capability was rejected"):
        broker.redeem(capability, task, "runner-a")
    assert encryptor.decrypt.call_count == 1


def test_concurrent_in_memory_redeem_has_exactly_one_winner() -> None:
    broker, task, _, _ = _setup()
    capability = broker.issue(task, "runner-a")

    def redeem() -> bool:
        try:
            broker.redeem(capability, task, "runner-a")
            return True
        except CapabilityRejected:
            return False

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: redeem(), range(16)))
    assert results.count(True) == 1