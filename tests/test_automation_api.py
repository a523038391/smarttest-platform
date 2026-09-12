from uuid import uuid4

from fastapi.testclient import TestClient

from services.api.app import create_app


DIGEST = "e" * 64


def script_payload(project_id: str) -> dict:
    return {
        "project_id": project_id,
        "name": "Login test",
        "description": "",
        "engine": "pytest",
        "entrypoint": "tests/test_login.py",
        "source_ref": "artifact:login-v1",
        "content_digest": DIGEST,
        "timeout_seconds": 300,
    }


def case_payload(project_id: str) -> dict:
    return {
        "project_id": project_id,
        "title": "Valid login",
        "description": "",
        "preconditions": "Account exists",
        "priority": "HIGH",
        "steps": [{
            "order": 1,
            "action": "Submit credentials",
            "expected_result": "Dashboard is displayed",
        }],
    }


def test_script_crud_revisions_and_case_traceability() -> None:
    client = TestClient(create_app())
    project_id = str(uuid4())
    case = client.post("/api/v1/test-cases", json=case_payload(project_id)).json()
    created = client.post(
        "/api/v1/automation-scripts", json=script_payload(project_id)
    )
    script = created.json()
    update = script_payload(project_id)
    update.pop("project_id")
    update.pop("engine")
    update.update({
        "description": "Published", "status": "ACTIVE", "state_version": 0,
    })

    updated = client.put(
        f"/api/v1/automation-scripts/{script['id']}", json=update
    )
    linked = client.post(
        f"/api/v1/test-cases/{case['id']}/automation-scripts",
        json={"script_id": script["id"]},
    )
    replayed = client.post(
        f"/api/v1/test-cases/{case['id']}/automation-scripts",
        json={"script_id": script["id"]},
    )

    assert created.status_code == 201
    assert updated.status_code == 200
    assert updated.json()["revision"] == 2
    assert linked.status_code == 201
    assert replayed.status_code == 200
    assert client.get(
        f"/api/v1/automation-scripts/{script['id']}/revisions"
    ).json()["total"] == 2
    assert client.get(
        f"/api/v1/test-cases/{case['id']}/automation-scripts"
    ).json()["items"][0]["id"] == script["id"]
    assert client.get(
        "/api/v1/automation-scripts", params={"project_id": project_id}
    ).json()["total"] == 1


def test_script_api_rejects_unsafe_input_stale_update_and_unscoped_list() -> None:
    client = TestClient(create_app())
    project_id = str(uuid4())
    payload = script_payload(project_id)
    payload["entrypoint"] = "../secret.py"
    invalid = client.post("/api/v1/automation-scripts", json=payload)
    created = client.post(
        "/api/v1/automation-scripts", json=script_payload(project_id)
    ).json()
    update = script_payload(project_id)
    update.pop("project_id")
    update.pop("engine")
    update.update({"status": "ACTIVE", "state_version": 0})
    client.put(f"/api/v1/automation-scripts/{created['id']}", json=update)
    stale = client.put(
        f"/api/v1/automation-scripts/{created['id']}", json=update
    )

    assert invalid.status_code == 422
    assert stale.status_code == 409
    assert stale.json()["code"] == "asset_version_conflict"
    assert client.get("/api/v1/automation-scripts").status_code == 422


def test_script_api_reactivates_an_archived_script() -> None:
    client = TestClient(create_app())
    script = client.post(
        "/api/v1/automation-scripts", json=script_payload(str(uuid4()))
    ).json()

    for status in ("ACTIVE", "ARCHIVED", "ACTIVE"):
        response = client.put(
            f"/api/v1/automation-scripts/{script['id']}",
            json={
                "name": script["name"], "description": script["description"],
                "entrypoint": script["entrypoint"], "source_ref": script["source_ref"],
                "content_digest": script["content_digest"],
                "timeout_seconds": script["timeout_seconds"], "status": status,
                "state_version": script["state_version"],
            },
        )
        assert response.status_code == 200
        script = response.json()

    assert script["status"] == "ACTIVE"
    assert script["revision"] == 4


def test_script_api_reports_missing_and_cross_project_links() -> None:
    client = TestClient(create_app())
    missing = client.get(f"/api/v1/automation-scripts/{uuid4()}")
    case = client.post(
        "/api/v1/test-cases", json=case_payload(str(uuid4()))
    ).json()
    script = client.post(
        "/api/v1/automation-scripts", json=script_payload(str(uuid4()))
    ).json()
    conflict = client.post(
        f"/api/v1/test-cases/{case['id']}/automation-scripts",
        json={"script_id": script["id"]},
    )

    assert missing.status_code == 404
    assert missing.json()["code"] == "automation_script_not_found"
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "script_link_conflict"