from uuid import uuid4

import pytest

from packages.protocol import Engine
from services.api.automation_domain import AutomationScriptStatus
from services.api.automation_repository import AutomationRepository
from services.api.environment_crypto import SecretEncryptor
from services.api.environment_domain import ConfigurationValueInput, EnvironmentStatus
from services.api.environment_repository import EnvironmentRepository
from services.api.quality_repository import QualityRepository
from services.api.repository import RunRepository
from services.api.test_plan_domain import (
    InvalidTestPlanTransition, TestPlanDataRow as PlanDataRow,
    TestPlanItem as PlanItem, TestPlanStatus as PlanStatus,
)
from services.api.test_plan_repository import (
    RunSpecTaskMaterializer, TestPlanConflict as PlanConflict,
    TestPlanRepository as PlanRepository,
)


def active_assets(project_id):
    scripts = AutomationRepository(QualityRepository())
    draft = scripts.create_script(
        project_id, "login", "", Engine.PYTEST, "tests/test_login.py",
        "artifact:source-v1", "a" * 64, 90,
    )
    script = scripts.update_script(
        draft.id, expected_version=0, name=draft.name, description="",
        entrypoint=draft.entrypoint, source_ref=draft.source_ref,
        content_digest=draft.content_digest, timeout_seconds=draft.timeout_seconds,
        status=AutomationScriptStatus.ACTIVE,
    )
    environments = EnvironmentRepository(SecretEncryptor(b"k" * 32, "key-1"))
    created = environments.create_environment(
        project_id, "staging",
        [
            ConfigurationValueInput("BASE_URL", "https://example.test"),
            ConfigurationValueInput("TOKEN", "top-secret", secret=True),
        ],
        [
            ConfigurationValueInput("region", "environment"),
            ConfigurationValueInput("password", "hidden", secret=True),
        ],
    )
    values = [
        ConfigurationValueInput(
            item.name, secret=True, secret_ref=item.secret_ref
        ) if item.secret else ConfigurationValueInput(item.name, item.value)
        for item in created.values
    ]
    environment = environments.update_environment(
        created.id, expected_version=0, name=created.name,
        status=EnvironmentStatus.ACTIVE,
        environment_variables=values[:2], common_parameters=values[2:],
    )
    return scripts, script, environments, environment


def test_revisions_execution_merge_replay_snapshot_and_materialization() -> None:
    project_id, tenant_id = uuid4(), uuid4()
    scripts, script, environments, environment = active_assets(project_id)
    runs = RunRepository()
    repository = PlanRepository(scripts, environments, runs)
    item = PlanItem(
        uuid4(), script.id, script.revision, environment.id, environment.revision,
        {"region": "item", "attempts": 2},
        (
            PlanDataRow("first", {"attempts": 3, "user": "one"}),
            PlanDataRow("second", {"user": "two"}),
        ),
    )
    draft = repository.create_plan(tenant_id, project_id, "smoke", "", [item])
    active = repository.update_plan(
        draft.id, expected_version=0, name=draft.name, description="published",
        items=[item], status=PlanStatus.ACTIVE,
    )

    batch, replayed = repository.execute(active.id, "batch-key")
    repeated, was_replayed = repository.execute(active.id, "batch-key")
    first = repository.get_run_spec(batch.run_ids[0])
    second = repository.get_run_spec(batch.run_ids[1])

    assert not replayed and was_replayed and repeated.id == batch.id
    assert first.parameters == {"region": "item", "attempts": 3, "user": "one"}
    assert second.parameters == {"region": "item", "attempts": 2, "user": "two"}
    assert first.environment_variables == {"BASE_URL": "https://example.test"}
    assert {item.name for item in first.secret_references} == {"TOKEN", "password"}
    assert runs.get(first.run_id).parameters == first.parameters
    assert [item.revision for item in repository.list_revisions(active.id)] == [1, 2]

    scripts.update_script(
        script.id, expected_version=1, name=script.name, description="changed",
        entrypoint=script.entrypoint, source_ref="artifact:source-v2",
        content_digest="b" * 64, timeout_seconds=30,
        status=AutomationScriptStatus.ACTIVE,
    )
    assert repository.get_run_spec(first.run_id).source_ref == "artifact:source-v1"

    task = RunSpecTaskMaterializer(repository).materialize(first.run_id, uuid4())
    assert task.run_spec_id == first.id
    assert task.source_ref == first.source_ref
    assert task.content_digest == first.content_digest
    assert dict(task.environment_variables) == first.environment_variables
    assert dict(task.parameters) == first.parameters
    assert int((task.deadline - task.created_at).total_seconds()) == 90

    changed = repository.update_plan(
        active.id, expected_version=1, name=active.name, description="changed",
        items=[item], status=PlanStatus.ACTIVE,
    )
    with pytest.raises(PlanConflict, match="another plan revision"):
        repository.execute(changed.id, "batch-key")
    archived = repository.update_plan(
        changed.id, expected_version=2, name=changed.name, description="archived",
        items=[item], status=PlanStatus.ARCHIVED,
    )
    with pytest.raises(InvalidTestPlanTransition, match="cannot be modified"):
        repository.update_plan(
            archived.id, expected_version=3, name=archived.name, description="again",
            items=[item], status=PlanStatus.ARCHIVED,
        )


def test_plan_validation_rejects_inactive_cross_project_and_invalid_json() -> None:
    project_id = uuid4()
    scripts = AutomationRepository(QualityRepository())
    environments = EnvironmentRepository()
    repository = PlanRepository(scripts, environments, RunRepository())
    draft = scripts.create_script(
        project_id, "draft", "", Engine.HTTP, "case.json", "artifact:draft",
        "c" * 64, 10,
    )
    inactive = PlanItem(uuid4(), draft.id, 1, None, None, {}, ())
    with pytest.raises(PlanConflict, match="must be active"):
        repository.create_plan(uuid4(), project_id, "invalid", "", [inactive])

    other_project = uuid4()
    other_scripts, active, _, _ = active_assets(other_project)
    cross_project = PlanRepository(other_scripts, environments, RunRepository())
    item = PlanItem(uuid4(), active.id, active.revision, None, None, {}, ())
    with pytest.raises(PlanConflict, match="different project"):
        cross_project.create_plan(uuid4(), project_id, "invalid", "", [item])

    invalid = PlanItem(
        uuid4(), active.id, active.revision, None, None, {"bad": float("nan")}, ()
    )
    with pytest.raises(ValueError, match="JSON"):
        cross_project.create_plan(uuid4(), other_project, "invalid", "", [invalid])

    with pytest.raises(ValueError, match="at least one item"):
        cross_project.create_plan(uuid4(), other_project, "empty", "", [])