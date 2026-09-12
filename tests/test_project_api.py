from fastapi.testclient import TestClient

from packages.protocol import Engine
from services.api.app import create_app


def test_project_crud_list_and_archive() -> None:
    client = TestClient(create_app())

    created = client.post("/api/v1/projects", json={
        "name": "Checkout Platform", "description": "Payments squad",
    })
    assert created.status_code == 201
    body = created.json()
    assert body["name"] == "Checkout Platform"
    assert body["status"] == "ACTIVE"
    assert body["state_version"] == 0

    fetched = client.get(f"/api/v1/projects/{body['id']}")
    assert fetched.status_code == 200
    assert fetched.json() == body

    updated = client.put(f"/api/v1/projects/{body['id']}", json={
        "name": "Checkout Platform", "description": "Updated",
        "status": "ARCHIVED", "state_version": 0,
    })
    assert updated.status_code == 200
    assert updated.json()["status"] == "ARCHIVED"
    assert updated.json()["state_version"] == 1

    listed = client.get("/api/v1/projects")
    assert listed.status_code == 200
    assert listed.json()["total"] == 1

    active_only = client.get("/api/v1/projects", params={"status": "ACTIVE"})
    assert active_only.json()["total"] == 0


def test_project_api_rejects_duplicate_name_and_stale_version() -> None:
    client = TestClient(create_app())
    client.post("/api/v1/projects", json={"name": "Checkout", "description": ""})

    duplicate = client.post("/api/v1/projects", json={"name": "Checkout", "description": ""})
    assert duplicate.status_code == 409
    assert duplicate.json()["code"] == "project_name_conflict"

    other = client.post("/api/v1/projects", json={"name": "Billing", "description": ""}).json()
    client.put(f"/api/v1/projects/{other['id']}", json={
        "name": "Billing", "description": "v1", "status": "ACTIVE", "state_version": 0,
    })
    stale = client.put(f"/api/v1/projects/{other['id']}", json={
        "name": "Billing", "description": "v2", "status": "ACTIVE", "state_version": 0,
    })
    assert stale.status_code == 409
    assert stale.json()["code"] == "project_version_conflict"


def test_project_api_returns_404_for_missing_project() -> None:
    client = TestClient(create_app())
    response = client.get("/api/v1/projects/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404
    assert response.json()["code"] == "project_not_found"


def test_project_api_deletes_empty_project() -> None:
    client = TestClient(create_app())
    project = client.post("/api/v1/projects", json={
        "name": "Temporary", "description": "",
    }).json()

    deleted = client.delete(f"/api/v1/projects/{project['id']}")

    assert deleted.status_code == 204
    assert client.get(f"/api/v1/projects/{project['id']}").status_code == 404


def test_project_api_rejects_deleting_project_with_content() -> None:
    app = create_app()
    project = app.state.project_repository.create_project("ERP", "")
    app.state.automation_repository.create_script(
        project.id, "Inventory UI", "", Engine.PLAYWRIGHT,
        "tests/test_inventory.py", "local-source:fixture", "0" * 64, 300,
    )
    client = TestClient(app)

    deleted = client.delete(f"/api/v1/projects/{project.id}")

    assert deleted.status_code == 409
    assert deleted.json()["code"] == "project_in_use"
    assert client.get(f"/api/v1/projects/{project.id}").status_code == 200
