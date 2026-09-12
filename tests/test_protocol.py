from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from packages.protocol import (
    EventEnvelope,
    ResultEnvelope,
    SecretReference,
    TaskEnvelope,
)


def identity() -> dict:
    return {"run_id": uuid4(), "attempt_id": uuid4()}


@pytest.mark.parametrize("engine", ["http", "pytest", "playwright"])
def test_task_envelope_supports_engines_and_normalizes_utc(engine: str) -> None:
    created = datetime.now(timezone(timedelta(hours=8)))
    task = TaskEnvelope(
        **identity(),
        task_id=uuid4(),
        tenant_id=uuid4(),
        project_id=uuid4(),
        idempotency_key="task-1",
        engine=engine,
        created_at=created,
        deadline=created + timedelta(minutes=5),
        entrypoint="tests/test_example.py",
    )

    assert task.protocol_version == "1.0"
    assert task.created_at.utcoffset() == timedelta(0)


def test_all_envelopes_forbid_extra_fields() -> None:
    with pytest.raises(ValidationError, match="extra_forbidden"):
        EventEnvelope(
            **identity(),
            event_id=uuid4(),
            engine="pytest",
            seq=1,
            type="started",
            unexpected=True,
        )

    with pytest.raises(ValidationError, match="extra_forbidden"):
        ResultEnvelope(
            **identity(),
            engine="http",
            outcome="succeeded",
            duration_ms=1,
            unexpected=True,
        )


def test_protocol_rejects_unknown_version_and_naive_time() -> None:
    common = {
        **identity(),
        "event_id": uuid4(),
        "engine": "playwright",
        "seq": 0,
        "type": "heartbeat",
    }
    with pytest.raises(ValidationError):
        EventEnvelope(**common, protocol_version="2.0")
    with pytest.raises(ValidationError, match="timezone"):
        EventEnvelope(**common, occurred_at=datetime.now())


@pytest.mark.parametrize("deadline_delta", [timedelta(0), timedelta(seconds=-1)])
def test_task_deadline_must_follow_created_at(deadline_delta: timedelta) -> None:
    created = datetime.now(timezone.utc)
    with pytest.raises(ValidationError, match="deadline must be after created_at"):
        TaskEnvelope(
            **identity(),
            task_id=uuid4(),
            tenant_id=uuid4(),
            project_id=uuid4(),
            idempotency_key="task-key",
            engine="http",
            created_at=created,
            deadline=created + deadline_delta,
            entrypoint="case.json",
        )


def test_task_idempotency_key_is_stripped_and_rejects_blank() -> None:
    created = datetime.now(timezone.utc)
    common = {
        **identity(),
        "task_id": uuid4(),
        "tenant_id": uuid4(),
        "project_id": uuid4(),
        "engine": "pytest",
        "created_at": created,
        "deadline": created + timedelta(minutes=1),
        "entrypoint": "test_case.py",
    }

    assert TaskEnvelope(**common, idempotency_key="  task-key  ").idempotency_key == "task-key"
    with pytest.raises(ValidationError, match="must not be blank"):
        TaskEnvelope(**common, idempotency_key="   ")


def test_envelope_data_is_deeply_immutable_and_json_serializable() -> None:
    now = datetime.now(timezone.utc)
    nested = {"config": {"steps": [{"name": "first"}]}}
    envelopes = [
        (TaskEnvelope(
            **identity(), task_id=uuid4(), tenant_id=uuid4(), project_id=uuid4(),
            idempotency_key="key", engine="http", created_at=now,
            deadline=now + timedelta(minutes=1), entrypoint="case.json", parameters=nested,
        ), "parameters"),
        (EventEnvelope(
            **identity(), event_id=uuid4(), engine="pytest", seq=1,
            type="progress", occurred_at=now, payload=nested,
        ), "payload"),
        (ResultEnvelope(
            **identity(), engine="playwright", outcome="succeeded",
            completed_at=now, duration_ms=1, summary=nested,
        ), "summary"),
    ]

    for envelope, field_name in envelopes:
        value = getattr(envelope, field_name)
        with pytest.raises(TypeError):
            value["new"] = True
        with pytest.raises(TypeError):
            value["config"]["new"] = True
        with pytest.raises(AttributeError):
            value["config"]["steps"].append({"name": "second"})
        with pytest.raises(TypeError):
            value["config"]["steps"][0]["name"] = "changed"
        assert envelope.model_dump(mode="json")[field_name] == nested


def test_task_environment_pin_and_secret_references_are_typed_and_immutable() -> None:
    now = datetime.now(timezone.utc)
    environment_id = uuid4()
    task = TaskEnvelope(
        **identity(), task_id=uuid4(), tenant_id=uuid4(), project_id=uuid4(),
        idempotency_key="environment", engine="pytest", created_at=now,
        deadline=now + timedelta(minutes=1), entrypoint="test.py",
        environment_id=environment_id, environment_revision=2,
        secret_references=[SecretReference(
            category="environment_variable", name="TOKEN", secret_ref=uuid4()
        )],
    )

    assert task.protocol_version == "1.0"
    assert isinstance(task.secret_references, tuple)
    assert dict(task.environment_variables) == {}
    with pytest.raises(ValidationError, match="provided together"):
        TaskEnvelope(
            **identity(), task_id=uuid4(), tenant_id=uuid4(), project_id=uuid4(),
            idempotency_key="invalid", engine="http", created_at=now,
            deadline=now + timedelta(minutes=1), entrypoint="case.json",
            environment_id=environment_id,
        )


def test_task_optional_run_spec_source_fields_are_backward_compatible() -> None:
    now = datetime.now(timezone.utc)
    task = TaskEnvelope(
        **identity(), task_id=uuid4(), tenant_id=uuid4(), project_id=uuid4(),
        idempotency_key="run-spec", engine="http", created_at=now,
        deadline=now + timedelta(minutes=1), entrypoint="case.json",
        run_spec_id=uuid4(), source_ref="artifact:case", content_digest="a" * 64,
        environment_variables={"BASE_URL": "https://example.test"},
    )

    assert task.run_spec_id is not None
    assert dict(task.environment_variables) == {"BASE_URL": "https://example.test"}
    with pytest.raises(TypeError):
        task.environment_variables["OTHER"] = "value"

    with pytest.raises(ValidationError, match="valid names"):
        TaskEnvelope(
            **identity(), task_id=uuid4(), tenant_id=uuid4(), project_id=uuid4(),
            idempotency_key="invalid-env", engine="http", created_at=now,
            deadline=now + timedelta(minutes=1), entrypoint="case.json",
            environment_variables={"INVALID-NAME": "value"},
        )


def test_task_rejects_duplicate_secret_reference_ids() -> None:
    now = datetime.now(timezone.utc)
    reference_id = uuid4()
    with pytest.raises(ValidationError, match="reference IDs must be unique"):
        TaskEnvelope(
            **identity(), task_id=uuid4(), tenant_id=uuid4(), project_id=uuid4(),
            idempotency_key="key", engine="http", created_at=now,
            deadline=now + timedelta(minutes=1), entrypoint="case.json",
            environment_id=uuid4(), environment_revision=1,
            secret_references=(
                SecretReference(
                    category="environment_variable", name="TOKEN",
                    secret_ref=reference_id,
                ),
                SecretReference(
                    category="common_parameter", name="password",
                    secret_ref=reference_id,
                ),
            ),
        )