from collections.abc import Iterator
from uuid import uuid4

import pytest
from sqlalchemy import Engine as SqlEngine
from sqlalchemy.orm import sessionmaker

from services.api.database import Base, create_database_engine, create_session_factory
from services.api.project_domain import ProjectStatus
from services.api.project_repository import ProjectNameConflict, ProjectNotFound
from services.api.project_sql_repository import SqlProjectRepository


@pytest.fixture
def database(tmp_path) -> Iterator[tuple[SqlEngine, sessionmaker]]:
    engine = create_database_engine(
        f"sqlite+pysqlite:///{(tmp_path / 'projects.sqlite3').as_posix()}"
    )
    Base.metadata.create_all(engine)
    yield engine, create_session_factory(engine)
    engine.dispose()


def test_sql_project_lifecycle_is_persisted(database) -> None:
    _, sessions = database
    repository = SqlProjectRepository(sessions)

    created = repository.create_project("Checkout Platform", "Payments squad")
    archived = repository.update_project(
        created.id, name=created.name, description="Wrapping up",
        status=ProjectStatus.ARCHIVED, expected_version=0,
    )

    assert repository.get_project(created.id) == archived
    assert repository.list_projects() == [archived]
    assert repository.list_projects(ProjectStatus.ARCHIVED) == [archived]
    assert repository.list_projects(ProjectStatus.ACTIVE) == []


def test_sql_project_not_found(database) -> None:
    _, sessions = database
    repository = SqlProjectRepository(sessions)
    with pytest.raises(ProjectNotFound):
        repository.get_project(uuid4())


def test_sql_delete_project_is_persisted(database) -> None:
    _, sessions = database
    repository = SqlProjectRepository(sessions)
    project = repository.create_project("Checkout", "")

    repository.delete_project(project.id)

    assert repository.list_projects() == []
    with pytest.raises(ProjectNotFound):
        repository.get_project(project.id)


def test_sql_duplicate_project_name_is_rejected(database) -> None:
    _, sessions = database
    repository = SqlProjectRepository(sessions)
    repository.create_project("Checkout", "")

    with pytest.raises(ProjectNameConflict):
        repository.create_project("Checkout", "")


def test_sql_rename_to_existing_name_is_rejected(database) -> None:
    _, sessions = database
    repository = SqlProjectRepository(sessions)
    repository.create_project("Checkout", "")
    other = repository.create_project("Billing", "")

    with pytest.raises(ProjectNameConflict):
        repository.update_project(
            other.id, name="Checkout", description="",
            status=ProjectStatus.ACTIVE, expected_version=0,
        )
