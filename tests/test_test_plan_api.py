from uuid import uuid4

from fastapi.testclient import TestClient

from packages.protocol import Engine
from services.api.app import create_app
from services.api.automation_domain import AutomationScriptStatus
from services.api.config import Settings
from services.api.environment_crypto import SecretEncryptor
from services.api.environment_domain import ConfigurationValueInput, EnvironmentStatus
from services.api.environment_repository import EnvironmentRepository


class RecordingDispatcher:
    def __init__(self) -> None:
        self.run_ids = []

    def dispatch(self, run_id) -> None:
        self.run_ids.append(run_id)


def test_test_plan_api_execution_and_secret_safe_run_spec() -> None:
    key = b"z" * 32
    dispatcher = RecordingDispatcher()
    app = create_app(
        settings=Settings(auto_dispatch=True, auth_required=False), dispatcher=dispatcher,
        environment_repository=EnvironmentRepository(SecretEncryptor(key, "key-1")),
    )
    project_id, tenant_id = uuid4(), uuid4()
    script = app.state.automation_repository.create_script(
        project_id, "login", "", Engine.PYTEST, "test_login.py",
        "artifact:login", "e" * 64, 60,
    )
    script = app.state.automation_repository.update_script(
        script.id, expected_version=0, name=script.name, description="",
        entrypoint=script.entrypoint, source_ref=script.source_ref,
        content_digest=script.content_digest, timeout_seconds=60,
        status=AutomationScriptStatus.ACTIVE,
    )
    environment = app.state.environment_repository.create_environment(
        project_id, "staging", [
            ConfigurationValueInput("BASE_URL", "https://example.test"),
            ConfigurationValueInput("TOKEN", "never-expose-this", secret=True),
        ], [],
    )
    environment = app.state.environment_repository.update_environment(
        environment.id, expected_version=0, name=environment.name,
        status=EnvironmentStatus.ACTIVE,
        environment_variables=[
            ConfigurationValueInput("BASE_URL", "https://example.test"),
            ConfigurationValueInput(
                "TOKEN", secret=True,
                secret_ref=next(item.secret_ref for item in environment.values if item.secret),
            ),
        ], common_parameters=[],
    )
    client = TestClient(app)
    payload = {
        "tenant_id": str(tenant_id), "project_id": str(project_id),
        "name": "smoke", "description": "",
        "items": [{
            "script_id": str(script.id), "script_revision": script.revision,
            "environment_id": str(environment.id),
            "environment_revision": environment.revision,
            "default_parameters": {"role": "member"},
        }],
    }
    created = client.post("/api/v1/test-plans", json=payload)
    assert created.status_code == 201
    body = created.json()
    assert body["items"][0]["data_rows"] == [{"row_key": "default", "values": {}}]
    update = {
        "name": body["name"], "description": body["description"],
        "items": body["items"], "status": "ACTIVE", "state_version": 0,
    }
    active = client.put(f"/api/v1/test-plans/{body['id']}", json=update)
    assert active.status_code == 200
    first = client.post(
        f"/api/v1/test-plans/{body['id']}/executions",
        headers={"Idempotency-Key": "api-key"},
    )
    second = client.post(
        f"/api/v1/test-plans/{body['id']}/executions",
        headers={"Idempotency-Key": "api-key"},
    )
    assert first.status_code == 201 and second.status_code == 200
    assert first.json()["id"] == second.json()["id"]
    assert len(dispatcher.run_ids) == 1
    run_id = first.json()["run_ids"][0]
    assert str(dispatcher.run_ids[0]) == run_id
    assert app.state.run_repository.get(dispatcher.run_ids[0]).state.value == "QUEUED"
    spec = client.get(f"/api/v1/runs/{run_id}/run-spec")
    assert spec.status_code == 200
    assert spec.json()["environment_variables"] == {
        "BASE_URL": "https://example.test"
    }
    assert spec.json()["parameters"] == {"role": "member"}
    assert spec.json()["secret_references"][0]["name"] == "TOKEN"
    assert "never-expose-this" not in created.text + active.text + first.text + spec.text
    assert client.get(
        "/api/v1/test-plans", params={"project_id": str(project_id)}
    ).json()["total"] == 1
    assert client.get(
        f"/api/v1/test-plans/{body['id']}/revisions"
    ).json()["total"] == 2
    revision = client.get(
        f"/api/v1/test-plans/{body['id']}/revisions/1"
    )
    assert revision.status_code == 200
    assert revision.json()["status"] == "DRAFT"