from uuid import uuid4

import pytest

from packages.protocol import Engine
from services.api.automation_domain import AutomationScriptStatus
from services.api.automation_repository import (
    AutomationRepository,
    ScriptLinkConflict,
)
from services.api.quality_domain import (
    CaseStepRecord,
    InvalidAssetTransition,
    TestCasePriority as CasePriority,
)
from services.api.quality_repository import AssetVersionConflict, QualityRepository


DIGEST = "a" * 64


def create_case(repository: QualityRepository, project_id):
    return repository.create_test_case(
        project_id, "Valid login", "", "Account exists",
        CasePriority.HIGH,
        (CaseStepRecord(1, "Sign in", "Dashboard is displayed"),),
    )


def test_script_revisions_state_machine_and_optimistic_version() -> None:
    quality = QualityRepository()
    repository = AutomationRepository(quality)
    script = repository.create_script(
        uuid4(), "Login API", "", Engine.PYTEST, "tests/test_login.py",
        "artifact:login-v1", DIGEST, 300,
    )

    active = repository.update_script(
        script.id, expected_version=0, name=script.name,
        description="Published", entrypoint=script.entrypoint,
        source_ref="artifact:login-v2", content_digest="b" * 64,
        timeout_seconds=120, status=AutomationScriptStatus.ACTIVE,
    )

    assert active.engine is Engine.PYTEST
    assert active.revision == 2
    assert [item.revision for item in repository.list_revisions(script.id)] == [1, 2]
    with pytest.raises(AssetVersionConflict):
        repository.update_script(
            script.id, expected_version=0, name=script.name,
            description="Stale", entrypoint=script.entrypoint,
            source_ref=script.source_ref, content_digest=DIGEST,
            timeout_seconds=300, status=AutomationScriptStatus.ACTIVE,
        )


def test_archived_script_can_be_reactivated_but_not_edited() -> None:
    repository = AutomationRepository(QualityRepository())
    script = repository.create_script(
        uuid4(), "Login", "", Engine.PYTEST, "test_login.py",
        "artifact:login", DIGEST, 30,
    )
    active = repository.update_script(
        script.id, expected_version=0, name=script.name, description="",
        entrypoint=script.entrypoint, source_ref=script.source_ref,
        content_digest=DIGEST, timeout_seconds=30,
        status=AutomationScriptStatus.ACTIVE,
    )
    archived = repository.update_script(
        script.id, expected_version=active.state_version, name=active.name,
        description="", entrypoint=active.entrypoint, source_ref=active.source_ref,
        content_digest=DIGEST, timeout_seconds=30,
        status=AutomationScriptStatus.ARCHIVED,
    )

    with pytest.raises(InvalidAssetTransition, match="while it is archived"):
        repository.update_script(
            script.id, expected_version=archived.state_version, name="Changed",
            description="", entrypoint=archived.entrypoint,
            source_ref=archived.source_ref, content_digest=DIGEST,
            timeout_seconds=30, status=AutomationScriptStatus.ARCHIVED,
        )

    restored = repository.update_script(
        script.id, expected_version=archived.state_version, name=archived.name,
        description="", entrypoint=archived.entrypoint,
        source_ref=archived.source_ref, content_digest=DIGEST,
        timeout_seconds=30, status=AutomationScriptStatus.ACTIVE,
    )
    assert restored.status is AutomationScriptStatus.ACTIVE
    assert restored.revision == 4


def test_case_script_link_is_project_scoped_idempotent_and_historical() -> None:
    quality = QualityRepository()
    repository = AutomationRepository(quality)
    project_id = uuid4()
    case = create_case(quality, project_id)
    script = repository.create_script(
        project_id, "Login", "", Engine.PLAYWRIGHT, "login.py",
        "artifact:login", DIGEST, 300,
    )

    link, replayed = repository.link_test_case(case.id, script.id)
    repeated, was_replayed = repository.link_test_case(case.id, script.id)

    assert replayed is False
    assert was_replayed is True
    assert repeated == link
    assert repository.list_test_case_scripts(case.id) == [script]

    active = repository.update_script(
        script.id, expected_version=0, name=script.name,
        description=script.description, entrypoint=script.entrypoint,
        source_ref=script.source_ref, content_digest=script.content_digest,
        timeout_seconds=script.timeout_seconds,
        status=AutomationScriptStatus.ACTIVE,
    )
    repository.update_script(
        script.id, expected_version=active.state_version, name=active.name,
        description=active.description, entrypoint=active.entrypoint,
        source_ref=active.source_ref, content_digest=active.content_digest,
        timeout_seconds=active.timeout_seconds,
        status=AutomationScriptStatus.ARCHIVED,
    )
    _, replay_after_archive = repository.link_test_case(case.id, script.id)
    assert replay_after_archive is True

    foreign = repository.create_script(
        uuid4(), "Foreign", "", Engine.HTTP, "request.json",
        "artifact:foreign", DIGEST, 30,
    )
    with pytest.raises(ScriptLinkConflict, match="different projects"):
        repository.link_test_case(case.id, foreign.id)

    archived_unlinked = repository.create_script(
        project_id, "Old", "", Engine.PYTEST, "test_old.py",
        "artifact:old", DIGEST, 30,
    )
    old_active = repository.update_script(
        archived_unlinked.id, expected_version=0, name=archived_unlinked.name,
        description="", entrypoint=archived_unlinked.entrypoint,
        source_ref=archived_unlinked.source_ref, content_digest=DIGEST,
        timeout_seconds=30, status=AutomationScriptStatus.ACTIVE,
    )
    repository.update_script(
        archived_unlinked.id, expected_version=old_active.state_version,
        name=old_active.name, description="", entrypoint=old_active.entrypoint,
        source_ref=old_active.source_ref, content_digest=DIGEST,
        timeout_seconds=30, status=AutomationScriptStatus.ARCHIVED,
    )
    with pytest.raises(ScriptLinkConflict, match="archived automation script"):
        repository.link_test_case(case.id, archived_unlinked.id)