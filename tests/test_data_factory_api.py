from datetime import datetime, timezone
from uuid import uuid4

from fastapi.testclient import TestClient

from services.api.app import create_app
from services.api.config import Settings
from services.api.data_factory_executor import DataFactoryExecutor, WorkflowRunRecord


class StubExecutor(DataFactoryExecutor):
    def _run(self, workflow_id: str, node_id: str) -> WorkflowRunRecord:
        now = datetime.now(timezone.utc).isoformat()
        return WorkflowRunRecord(
            str(uuid4()), workflow_id, "SUCCEEDED",
            {node_id: {"status": "SUCCEEDED"}}, {"output": node_id}, now, now,
        )

    def execute(self, workflow, variables=None, *, workflow_id=None):
        return self._run(str(workflow_id), "execute")

    def debug(self, workflow, node_id, variables=None, *, workflow_id=None):
        return self._run(str(workflow_id), node_id)


def make_test_app() -> object:
    return create_app(
        settings=Settings(database_url=None, auth_required=False),
        data_factory_executor=StubExecutor(),
    )


def workflow_payload(project_id: str, name: str = "Prepare users") -> dict:
    return {
        "project_id": project_id, "name": name, "description": "fixture",
        "nodes": [{
            "id": "gate", "name": "Gate", "type": "condition", "x": 10, "y": 20,
            "config": {"left": "{{enabled}}", "operator": "equals", "right": True},
        }],
        "edges": [], "variables": {"enabled": True},
    }


def create_project(client: TestClient, name: str = "Factory") -> dict:
    response = client.post("/api/v1/projects", json={"name": name, "description": ""})
    assert response.status_code == 201
    return response.json()


def test_data_factory_workflow_crud_and_conflicts() -> None:
    client = TestClient(make_test_app())
    project = create_project(client)
    created = client.post(
        "/api/v1/data-factory/workflows", json=workflow_payload(project["id"])
    )
    assert created.status_code == 201
    workflow = created.json()
    assert workflow["state_version"] == 0
    assert workflow["nodes"][0]["config"]["operator"] == "equals"
    assert client.get(
        "/api/v1/data-factory/workflows", params={"project_id": project["id"]}
    ).json()["total"] == 1
    assert client.get(
        f"/api/v1/data-factory/workflows/{workflow['id']}"
    ).json() == workflow

    update = {**workflow_payload(project["id"]), "description": "updated", "state_version": 0}
    updated = client.put(
        f"/api/v1/data-factory/workflows/{workflow['id']}", json=update
    )
    assert updated.status_code == 200
    assert updated.json()["state_version"] == 1
    assert client.put(
        f"/api/v1/data-factory/workflows/{workflow['id']}", json=update
    ).status_code == 409
    duplicate = client.post(
        "/api/v1/data-factory/workflows", json=workflow_payload(project["id"])
    )
    assert duplicate.status_code == 409

    deleted = client.delete(f"/api/v1/data-factory/workflows/{workflow['id']}")
    assert deleted.status_code == 204
    assert client.get(
        f"/api/v1/data-factory/workflows/{workflow['id']}"
    ).status_code == 404


def test_execute_is_persisted_and_debug_is_not() -> None:
    client = TestClient(make_test_app())
    project = create_project(client)
    workflow = client.post(
        "/api/v1/data-factory/workflows", json=workflow_payload(project["id"])
    ).json()

    executed = client.post(
        f"/api/v1/data-factory/workflows/{workflow['id']}/execute",
        json={"variables": {"enabled": False}},
    )
    debugged = client.post(
        f"/api/v1/data-factory/workflows/{workflow['id']}/debug",
        json={"node_id": "gate", "variables": {}},
    )
    runs = client.get(
        f"/api/v1/data-factory/workflows/{workflow['id']}/runs"
    )

    assert executed.status_code == debugged.status_code == 200
    assert debugged.json()["node_results"] == {"gate": {"status": "SUCCEEDED"}}
    assert runs.json()["total"] == 1
    assert runs.json()["items"][0]["id"] == executed.json()["id"]


def test_create_validates_project_and_project_delete_is_protected() -> None:
    client = TestClient(make_test_app())
    missing = client.post(
        "/api/v1/data-factory/workflows", json=workflow_payload(str(uuid4()))
    )
    assert missing.status_code == 404
    assert missing.json()["code"] == "project_not_found"

    project = create_project(client, "Protected")
    client.post(
        "/api/v1/data-factory/workflows", json=workflow_payload(project["id"])
    )
    deleted = client.delete(f"/api/v1/projects/{project['id']}")
    assert deleted.status_code == 409
    assert deleted.json()["code"] == "project_in_use"