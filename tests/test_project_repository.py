from uuid import uuid4

import pytest

from services.api.project_domain import InvalidProjectTransition, ProjectStatus
from services.api.project_repository import (
    ProjectNameConflict,
    ProjectNotFound,
    ProjectRepository,
    ProjectVersionConflict,
)


def test_create_list_update_and_archive_project() -> None:
    repository = ProjectRepository()
    project = repository.create_project("  Checkout Platform  ", "Payments squad")

    assert project.name == "Checkout Platform"
    assert project.status is ProjectStatus.ACTIVE
    assert project.state_version == 0

    renamed = repository.update_project(
        project.id, name="Checkout Platform", description="Updated",
        status=ProjectStatus.ACTIVE, expected_version=0,
    )
    assert renamed.description == "Updated"
    assert renamed.state_version == 1

    archived = repository.update_project(
        renamed.id, name=renamed.name, description=renamed.description,
        status=ProjectStatus.ARCHIVED, expected_version=1,
    )
    assert archived.status is ProjectStatus.ARCHIVED

    assert repository.list_projects() == [archived]
    assert repository.list_projects(ProjectStatus.ACTIVE) == []
    assert repository.list_projects(ProjectStatus.ARCHIVED) == [archived]


def test_get_project_not_found() -> None:
    repository = ProjectRepository()
    with pytest.raises(ProjectNotFound):
        repository.get_project(uuid4())


def test_delete_project_removes_it_and_releases_its_name() -> None:
    repository = ProjectRepository()
    project = repository.create_project("Checkout", "")

    repository.delete_project(project.id)

    with pytest.raises(ProjectNotFound):
        repository.get_project(project.id)
    replacement = repository.create_project("Checkout", "replacement")
    assert replacement.name == "Checkout"


def test_duplicate_project_name_is_rejected() -> None:
    repository = ProjectRepository()
    repository.create_project("Checkout", "")

    with pytest.raises(ProjectNameConflict):
        repository.create_project("Checkout", "")


def test_rename_to_existing_project_name_is_rejected() -> None:
    repository = ProjectRepository()
    repository.create_project("Checkout", "")
    other = repository.create_project("Billing", "")

    with pytest.raises(ProjectNameConflict):
        repository.update_project(
            other.id, name="Checkout", description="",
            status=ProjectStatus.ACTIVE, expected_version=0,
        )


def test_stale_update_is_rejected() -> None:
    repository = ProjectRepository()
    project = repository.create_project("Checkout", "")
    repository.update_project(
        project.id, name="Checkout", description="v1",
        status=ProjectStatus.ACTIVE, expected_version=0,
    )

    with pytest.raises(ProjectVersionConflict):
        repository.update_project(
            project.id, name="Checkout", description="v2",
            status=ProjectStatus.ACTIVE, expected_version=0,
        )


def test_archived_project_cannot_be_modified() -> None:
    repository = ProjectRepository()
    project = repository.create_project("Checkout", "")
    archived = repository.update_project(
        project.id, name="Checkout", description="",
        status=ProjectStatus.ARCHIVED, expected_version=0,
    )

    with pytest.raises(InvalidProjectTransition):
        repository.update_project(
            archived.id, name="Checkout", description="changed",
            status=ProjectStatus.ARCHIVED, expected_version=1,
        )
    with pytest.raises(InvalidProjectTransition):
        repository.update_project(
            archived.id, name="Checkout", description="",
            status=ProjectStatus.ACTIVE, expected_version=1,
        )


def test_blank_project_name_is_rejected() -> None:
    repository = ProjectRepository()
    with pytest.raises(ValueError):
        repository.create_project("   ", "")
