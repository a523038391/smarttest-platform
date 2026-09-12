from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from services.api.data_factory_executor import WorkflowRunRecord
from services.api.data_factory_repository import (
    WorkflowConflict, WorkflowNameConflict, WorkflowVersionConflict,
)
from services.api.data_factory_sql_repository import SqlDataFactoryRepository
from services.api.database import Base, create_database_engine, create_session_factory
from services.api.project_sql_repository import SqlProjectRepository


def definition() -> tuple[list[dict], list[dict], dict]:
    nodes = [{
        "id": "request", "name": "Request", "type": "http", "x": 0, "y": 0,
        "config": {"method": "get", "url": "https://example.com"},
    }]
    return nodes, [], {"tenant": "acme"}


def test_sql_workflow_crud_normalization_and_conflicts(tmp_path) -> None:
    engine = create_database_engine(
        f"sqlite+pysqlite:///{(tmp_path / 'factory.sqlite3').as_posix()}"
    )
    Base.metadata.create_all(engine)
    sessions = create_session_factory(engine)
    project = SqlProjectRepository(sessions).create_project("Factory SQL", "")
    other = SqlProjectRepository(sessions).create_project("Other SQL", "")
    repository = SqlDataFactoryRepository(sessions)
    nodes, edges, variables = definition()

    created = repository.create_workflow(
        project.id, "  Seed users  ", "initial", nodes, edges, variables
    )
    assert created.name == "Seed users"
    assert created.nodes[0]["config"]["method"] == "GET"
    assert created.created_at.tzinfo is not None
    assert SqlDataFactoryRepository(sessions).get_workflow(created.id) == created
    with pytest.raises(WorkflowNameConflict):
        repository.create_workflow(
            project.id, "Seed users", "duplicate", nodes, edges, variables
        )
    with pytest.raises(WorkflowConflict, match="moved"):
        repository.update_workflow(
            created.id, project_id=other.id, name=created.name, description="",
            nodes=nodes, edges=edges, variables=variables, expected_version=0,
        )

    updated = repository.update_workflow(
        created.id, project_id=project.id, name=created.name, description="updated",
        nodes=nodes, edges=edges, variables=variables, expected_version=0,
    )
    assert updated.state_version == 1
    with pytest.raises(WorkflowVersionConflict):
        repository.update_workflow(
            created.id, project_id=project.id, name=created.name, description="stale",
            nodes=nodes, edges=edges, variables=variables, expected_version=0,
        )
    assert repository.list_workflows(project.id) == [updated]
    engine.dispose()


def test_sql_full_runs_round_trip_newest_first(tmp_path) -> None:
    engine = create_database_engine(
        f"sqlite+pysqlite:///{(tmp_path / 'factory-runs.sqlite3').as_posix()}"
    )
    Base.metadata.create_all(engine)
    sessions = create_session_factory(engine)
    project = SqlProjectRepository(sessions).create_project("Run SQL", "")
    repository = SqlDataFactoryRepository(sessions)
    nodes, edges, variables = definition()
    workflow = repository.create_workflow(
        project.id, "Runs", "", nodes, edges, variables
    )
    started = datetime.now(timezone.utc)
    older = WorkflowRunRecord(
        str(uuid4()), str(workflow.id), "FAILED", {"request": {"status": "FAILED"}},
        {"tenant": "old"}, started.isoformat(), (started + timedelta(seconds=1)).isoformat(),
    )
    newer = WorkflowRunRecord(
        str(uuid4()), str(workflow.id), "SUCCEEDED",
        {"request": {"status": "SUCCEEDED", "status_code": 200}},
        {"tenant": "new"}, (started + timedelta(minutes=1)).isoformat(),
        (started + timedelta(minutes=1, seconds=1)).isoformat(),
    )

    repository.save_run(workflow.id, older)
    repository.save_run(workflow.id, newer)
    reloaded = SqlDataFactoryRepository(sessions).list_runs(workflow.id)

    assert reloaded == [newer, older]
    assert datetime.fromisoformat(reloaded[0].started_at).tzinfo is not None
    engine.dispose()