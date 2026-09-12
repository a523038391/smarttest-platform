from uuid import uuid4

from fastapi.testclient import TestClient

from services.api.app import create_app


def script_payload(project_id: str) -> dict:
    return {
        "project_id": project_id, "name": "Parameterized", "description": "",
        "engine": "pytest", "entrypoint": "test_parameters.py",
        "source_ref": "artifact:parameters", "content_digest": "f" * 64,
        "timeout_seconds": 60,
    }


def enum_payload(project_id: str, name: str = "Enabled") -> dict:
    return {
        "project_id": project_id, "name": name, "description": "flags",
        "options": [
            {"label": "False", "value": False},
            {"label": "Zero", "value": 0},
        ],
    }


def test_parameter_enum_crud_and_scalar_identity() -> None:
    client = TestClient(create_app())
    project_id = str(uuid4())
    created = client.post("/api/v1/parameter-enums", json=enum_payload(project_id))

    assert created.status_code == 201
    enum_set = created.json()
    assert enum_set["state_version"] == 0
    assert client.get(
        "/api/v1/parameter-enums", params={"project_id": project_id}
    ).json()["items"] == [enum_set]
    update = client.put(f"/api/v1/parameter-enums/{enum_set['id']}", json={
        "name": "Enabled", "description": "updated",
        "options": enum_set["options"], "state_version": 0,
    })
    assert update.status_code == 200
    assert update.json()["state_version"] == 1
    stale = client.put(f"/api/v1/parameter-enums/{enum_set['id']}", json={
        "name": "Enabled", "description": "stale",
        "options": enum_set["options"], "state_version": 0,
    })
    assert stale.status_code == 409
    assert stale.json()["code"] == "parameter_enum_version_conflict"


def test_parameter_enum_validation_and_project_name_uniqueness() -> None:
    client = TestClient(create_app())
    project_id = str(uuid4())
    client.post("/api/v1/parameter-enums", json=enum_payload(project_id))

    duplicate_name = client.post(
        "/api/v1/parameter-enums", json=enum_payload(project_id)
    )
    duplicate_value = enum_payload(project_id, "Duplicate values")
    duplicate_value["options"] = [
        {"label": "One", "value": "same"},
        {"label": "Two", "value": "same"},
    ]

    assert duplicate_name.status_code == 409
    assert client.post(
        "/api/v1/parameter-enums", json=duplicate_value
    ).status_code == 422
    assert client.get("/api/v1/parameter-enums").status_code == 422


def test_project_with_parameter_enum_cannot_be_deleted() -> None:
    client = TestClient(create_app())
    project = client.post(
        "/api/v1/projects", json={"name": "Catalog", "description": ""}
    ).json()
    client.post("/api/v1/parameter-enums", json=enum_payload(project["id"]))

    deleted = client.delete(f"/api/v1/projects/{project['id']}")

    assert deleted.status_code == 409
    assert deleted.json()["code"] == "project_in_use"


def test_script_parameter_replacement_and_referenced_enum_conflict() -> None:
    client = TestClient(create_app())
    project_id = str(uuid4())
    script = client.post(
        "/api/v1/automation-scripts", json=script_payload(project_id)
    ).json()
    enum_set = client.post(
        "/api/v1/parameter-enums", json=enum_payload(project_id)
    ).json()
    parameters = [{
        "name": "enabled", "label": "Enabled", "input_type": "enum",
        "required": True, "enum_set_id": enum_set["id"],
        "default_value": False, "position": 0,
    }, {
        "name": "attempts", "label": "Attempts", "input_type": "integer",
        "required": False, "enum_set_id": None, "default_value": 2,
        "position": 1,
    }]

    replaced = client.put(
        f"/api/v1/automation-scripts/{script['id']}/parameters", json=parameters
    )

    assert replaced.status_code == 200
    assert replaced.json() == parameters
    assert client.get(
        f"/api/v1/automation-scripts/{script['id']}/parameters"
    ).json() == parameters
    conflict = client.delete(f"/api/v1/parameter-enums/{enum_set['id']}")
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "parameter_enum_conflict"
    assert client.put(
        f"/api/v1/automation-scripts/{script['id']}/parameters", json=[]
    ).status_code == 200
    assert client.delete(
        f"/api/v1/parameter-enums/{enum_set['id']}"
    ).status_code == 204


def test_script_parameter_rejects_invalid_defaults_and_cross_project_enums() -> None:
    client = TestClient(create_app())
    project_id = str(uuid4())
    script = client.post(
        "/api/v1/automation-scripts", json=script_payload(project_id)
    ).json()
    foreign = client.post(
        "/api/v1/parameter-enums", json=enum_payload(str(uuid4()))
    ).json()
    local = client.post(
        "/api/v1/parameter-enums", json=enum_payload(project_id)
    ).json()
    parameter = {
        "name": "enabled", "label": "Enabled", "input_type": "enum",
        "required": False, "enum_set_id": foreign["id"],
        "default_value": False, "position": 0,
    }

    cross_project = client.put(
        f"/api/v1/automation-scripts/{script['id']}/parameters", json=[parameter]
    )
    parameter["enum_set_id"] = local["id"]
    parameter["default_value"] = "missing"
    invalid_default = client.put(
        f"/api/v1/automation-scripts/{script['id']}/parameters", json=[parameter]
    )
    parameter["enum_set_id"] = None
    missing_enum = client.put(
        f"/api/v1/automation-scripts/{script['id']}/parameters", json=[parameter]
    )

    assert cross_project.status_code == 409
    assert invalid_default.status_code == 409
    assert missing_enum.status_code == 422