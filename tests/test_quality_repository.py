from uuid import uuid4

import pytest

from services.api.quality_domain import (
    CaseStepRecord,
    InvalidAssetTransition,
    RequirementStatus,
    TestCasePriority as CasePriority,
    TestCaseStatus as CaseStatus,
)
from services.api.quality_repository import (
    AssetVersionConflict,
    QualityRepository,
    TraceLinkConflict,
)


def test_requirement_revisions_and_optimistic_version() -> None:
    repository = QualityRepository()
    requirement = repository.create_requirement(uuid4(), "Login", "Users sign in")

    active = repository.update_requirement(
        requirement.id, "Login", "Password login", RequirementStatus.ACTIVE, 0
    )

    assert active.revision == 2
    assert active.state_version == 1
    assert len(repository.list_requirement_revisions(requirement.id)) == 2
    with pytest.raises(AssetVersionConflict):
        repository.update_requirement(
            requirement.id, "Stale", "", RequirementStatus.ACTIVE, 0
        )
    with pytest.raises(InvalidAssetTransition):
        repository.update_requirement(
            requirement.id, "Login", "", RequirementStatus.DRAFT, 1
        )


def test_archived_assets_are_immutable() -> None:
    repository = QualityRepository()
    project_id = uuid4()
    requirement = repository.create_requirement(project_id, "Login", "")
    active = repository.update_requirement(
        requirement.id, "Login", "", RequirementStatus.ACTIVE, 0
    )
    archived = repository.update_requirement(
        requirement.id, "Login", "", RequirementStatus.ARCHIVED,
        active.state_version,
    )

    with pytest.raises(InvalidAssetTransition, match="terminal state ARCHIVED"):
        repository.update_requirement(
            requirement.id, "Changed", "", RequirementStatus.ARCHIVED,
            archived.state_version,
        )

    case = repository.create_test_case(
        project_id, "Valid login", "", "", CasePriority.HIGH,
        (CaseStepRecord(1, "Sign in", "Dashboard is shown"),),
    )
    ready = repository.update_test_case(
        case.id, expected_version=0, title=case.title,
        description=case.description, preconditions=case.preconditions,
        priority=case.priority, status=CaseStatus.READY, steps=case.steps,
    )
    archived_case = repository.update_test_case(
        case.id, expected_version=ready.state_version, title=ready.title,
        description=ready.description, preconditions=ready.preconditions,
        priority=ready.priority, status=CaseStatus.ARCHIVED, steps=ready.steps,
    )

    with pytest.raises(InvalidAssetTransition, match="terminal state ARCHIVED"):
        repository.update_test_case(
            case.id, expected_version=archived_case.state_version,
            title="Changed", description=archived_case.description,
            preconditions=archived_case.preconditions,
            priority=archived_case.priority, status=CaseStatus.ARCHIVED,
            steps=archived_case.steps,
        )


def test_test_case_revisions_preserve_structured_steps() -> None:
    repository = QualityRepository()
    project_id = uuid4()
    steps = (CaseStepRecord(1, "Open login", "Form is visible"),)
    case = repository.create_test_case(
        project_id, "Valid login", "", "Account exists", CasePriority.HIGH, steps
    )

    ready = repository.update_test_case(
        case.id,
        expected_version=0,
        title=case.title,
        description=case.description,
        preconditions=case.preconditions,
        priority=case.priority,
        status=CaseStatus.READY,
        steps=case.steps,
    )

    assert ready.status is CaseStatus.READY
    assert ready.steps == steps
    assert [item.revision for item in repository.list_test_case_revisions(case.id)] == [1, 2]


def test_trace_link_is_project_scoped_and_idempotent() -> None:
    repository = QualityRepository()
    project_id = uuid4()
    requirement = repository.create_requirement(project_id, "Login", "")
    case = repository.create_test_case(
        project_id, "Login case", "", "", CasePriority.MEDIUM, ()
    )

    link, replayed = repository.link_requirement_test_case(requirement.id, case.id)
    repeated, was_replayed = repository.link_requirement_test_case(requirement.id, case.id)

    assert replayed is False
    assert was_replayed is True
    assert repeated == link
    assert repository.list_requirement_test_cases(requirement.id) == [case]

    foreign_case = repository.create_test_case(
        uuid4(), "Foreign", "", "", CasePriority.LOW, ()
    )
    with pytest.raises(TraceLinkConflict, match="different projects"):
        repository.link_requirement_test_case(requirement.id, foreign_case.id)