from uuid import uuid4

from fastapi.testclient import TestClient

from services.api.app import create_app


def case_payload(project_id: str) -> dict:
    return {
        "project_id": project_id,
        "title": "Valid login",
        "description": "",
        "preconditions": "Account exists",
        "priority": "HIGH",
        "steps": [{
            "order": 1,
            "action": "Submit valid credentials",
            "expected_result": "Dashboard is displayed",
        }],
    }


def test_requirement_case_crud_revisions_and_traceability() -> None:
    client = TestClient(create_app())
    project_id = str(uuid4())
    requirement = client.post("/api/v1/requirements", json={
        "project_id": project_id, "title": "Login", "description": "Initial",
    })
    test_case = client.post("/api/v1/test-cases", json=case_payload(project_id))

    assert requirement.status_code == 201
    assert test_case.status_code == 201
    requirement_id = requirement.json()["id"]
    case_id = test_case.json()["id"]
    updated = client.put(f"/api/v1/requirements/{requirement_id}", json={
        "title": "Login", "description": "Updated", "status": "ACTIVE",
        "state_version": 0,
    })
    linked = client.post(f"/api/v1/requirements/{requirement_id}/test-cases", json={
        "test_case_id": case_id,
    })
    replayed = client.post(f"/api/v1/requirements/{requirement_id}/test-cases", json={
        "test_case_id": case_id,
    })

    assert updated.json()["revision"] == 2
    assert linked.status_code == 201
    assert replayed.status_code == 200
    assert client.get(
        f"/api/v1/requirements/{requirement_id}/revisions"
    ).json()["total"] == 2
    coverage = client.get(
        f"/api/v1/requirements/{requirement_id}/test-cases"
    ).json()
    assert coverage["items"][0]["id"] == case_id
    assert client.get(
        "/api/v1/requirements", params={"project_id": project_id}
    ).json()["total"] == 1


def test_quality_api_rejects_stale_updates_and_invalid_steps() -> None:
    client = TestClient(create_app())
    project_id = str(uuid4())
    requirement = client.post("/api/v1/requirements", json={
        "project_id": project_id, "title": "Login",
    }).json()
    first = client.put(f"/api/v1/requirements/{requirement['id']}", json={
        "title": "Login", "description": "", "status": "ACTIVE", "state_version": 0,
    })
    stale = client.put(f"/api/v1/requirements/{requirement['id']}", json={
        "title": "Stale", "description": "", "status": "ACTIVE", "state_version": 0,
    })
    payload = case_payload(project_id)
    payload["steps"][0]["order"] = 2
    invalid_case = client.post("/api/v1/test-cases", json=payload)

    assert first.status_code == 200
    assert stale.status_code == 409
    assert stale.json()["code"] == "asset_version_conflict"
    assert invalid_case.status_code == 422
    assert invalid_case.json()["code"] == "validation_error"


def test_quality_lists_require_project_scope() -> None:
    client = TestClient(create_app())

    requirements = client.get("/api/v1/requirements")
    test_cases = client.get("/api/v1/test-cases")

    assert requirements.status_code == 422
    assert test_cases.status_code == 422
    assert requirements.json()["code"] == "validation_error"


def test_quality_api_not_found_and_cross_project_link_problem() -> None:
    client = TestClient(create_app())
    missing = client.get(f"/api/v1/requirements/{uuid4()}")
    requirement = client.post("/api/v1/requirements", json={
        "project_id": str(uuid4()), "title": "Requirement",
    }).json()
    case = client.post(
        "/api/v1/test-cases", json=case_payload(str(uuid4()))
    ).json()
    conflict = client.post(
        f"/api/v1/requirements/{requirement['id']}/test-cases",
        json={"test_case_id": case["id"]},
    )

    assert missing.status_code == 404
    assert missing.json()["code"] == "requirement_not_found"
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "trace_link_conflict"