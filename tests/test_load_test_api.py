import base64
import time
from threading import Event
from uuid import uuid4

from fastapi.testclient import TestClient

from services.api.app import create_app
from services.api.config import Settings
from services.api.load_test_domain import LoadTestMode
from services.api.load_test_executor import LoadTestExecutor
from services.api.load_test_repository import LoadTestRepository


class Response:
    status_code = 200

    def close(self) -> None:
        pass


def payload(project_id: str) -> dict:
    return {
        "project_id": project_id, "name": "Smoke load", "description": "",
        "targets": [{
            "name": "Health", "method": "GET", "url": "https://example.test/health",
            "headers": {}, "query": {}, "body": None, "expected_statuses": [200],
        }],
        "mode": "COUNT", "request_count": 1, "duration_seconds": 1,
        "concurrency": 1, "interval_ms": 0, "timeout_seconds": 5,
    }


def test_load_test_crud_validation_and_project_guard() -> None:
    client = TestClient(create_app())
    project = client.post("/api/v1/projects", json={"name": "Load", "description": ""}).json()
    created = client.post("/api/v1/load-tests", json=payload(project["id"]))
    assert created.status_code == 201
    body = created.json()
    assert body["state_version"] == 0
    assert body["traffic_mode"] == "REQUESTS"
    assert body["initial_variables"] == {}
    assert body["stop_on_failure"] is True
    assert body["targets"][0]["extractors"] == {}
    assert body["targets"][0]["think_time_ms"] == 0
    assert client.get("/api/v1/load-tests", params={"project_id": project["id"]}).json()["total"] == 1
    update = {key: value for key, value in payload(project["id"]).items()
              if key != "project_id"}
    update.update({"description": "updated", "state_version": 0})
    assert client.put(f"/api/v1/load-tests/{body['id']}", json=update).json()["state_version"] == 1
    assert client.put(f"/api/v1/load-tests/{body['id']}", json=update).status_code == 409
    assert client.delete(f"/api/v1/projects/{project['id']}").status_code == 409
    invalid = {**payload(project["id"]), "name": "Invalid", "concurrency": 101}
    assert client.post("/api/v1/load-tests", json=invalid).status_code == 422
    assert client.post("/api/v1/load-tests", json=payload(str(uuid4()))).status_code == 404


def test_scenario_contract_round_trip_and_validation() -> None:
    client = TestClient(create_app())
    project = client.post(
        "/api/v1/projects", json={"name": "Scenario", "description": ""}
    ).json()
    body = payload(project["id"])
    body.update({
        "traffic_mode": "SCENARIO", "initial_variables": {"seed": {"id": 1}},
        "stop_on_failure": False,
        "targets": [{
            **body["targets"][0], "url": "https://example.test/{{seed}}",
            "extractors": {"next_id": "data.items.0.id"}, "think_time_ms": 10,
        }],
    })
    created = client.post("/api/v1/load-tests", json=body)
    assert created.status_code == 201
    assert created.json()["traffic_mode"] == "SCENARIO"
    assert created.json()["initial_variables"] == {"seed": {"id": 1}}
    assert created.json()["targets"][0]["extractors"] == {
        "next_id": "data.items.0.id"
    }

    requests_body = {**body, "name": "Invalid mode", "traffic_mode": "REQUESTS"}
    assert client.post("/api/v1/load-tests", json=requests_body).status_code == 422
    invalid_name = {**body, "name": "Invalid extractor"}
    invalid_name["targets"] = [{
        **body["targets"][0], "extractors": {"not-safe!": "data.id"},
    }]
    assert client.post("/api/v1/load-tests", json=invalid_name).status_code == 422
    invalid_think = {**body, "name": "Invalid think"}
    invalid_think["targets"] = [{**body["targets"][0], "think_time_ms": 60_001}]
    assert client.post("/api/v1/load-tests", json=invalid_think).status_code == 422


def test_environment_id_round_trip_and_cross_project_rejection() -> None:
    client = TestClient(create_app())
    first = client.post(
        "/api/v1/projects", json={"name": "First load", "description": ""}
    ).json()
    second = client.post(
        "/api/v1/projects", json={"name": "Second load", "description": ""}
    ).json()
    environment = client.post("/api/v1/environments", json={
        "project_id": first["id"], "name": "staging", "environment_variables": [],
    }).json()
    body = {**payload(first["id"]), "environment_id": environment["id"]}

    created = client.post("/api/v1/load-tests", json=body)
    assert created.status_code == 201
    assert created.json()["environment_id"] == environment["id"]
    assert client.get(
        f"/api/v1/load-tests/{created.json()['id']}"
    ).json()["environment_id"] == environment["id"]
    assert client.get(
        "/api/v1/load-tests", params={"project_id": first["id"]}
    ).json()["items"][0]["environment_id"] == environment["id"]

    cross = {**payload(second["id"]), "name": "Cross", "environment_id": environment["id"]}
    rejected = client.post("/api/v1/load-tests", json=cross)
    assert rejected.status_code == 404
    assert rejected.json()["code"] == "environment_not_found"
    second_environment = client.post("/api/v1/environments", json={
        "project_id": second["id"], "name": "other", "environment_variables": [],
    }).json()
    update = {key: value for key, value in body.items() if key != "project_id"}
    update.update({"state_version": 0, "environment_id": second_environment["id"]})
    rejected_update = client.put(
        f"/api/v1/load-tests/{created.json()['id']}", json=update
    )
    assert rejected_update.status_code == 404
    assert rejected_update.json()["code"] == "environment_not_found"


def test_active_environment_token_is_resolved_in_background_without_echo() -> None:
    calls, token = [], uuid4().hex
    executor = LoadTestExecutor(
        request_callable=lambda **kwargs: calls.append(kwargs) or Response(),
        resolver=lambda _: ["93.184.216.34"],
    )
    settings = Settings.from_env({
        "AUTH_REQUIRED": "false",
        "SECRET_ENCRYPTION_KEY": base64.b64encode(bytes(range(32))).decode(),
        "SECRET_ENCRYPTION_KEY_ID": "load-test-key",
    })
    with TestClient(create_app(settings=settings, load_test_executor=executor)) as client:
        project = client.post(
            "/api/v1/projects", json={"name": "Authenticated load", "description": ""}
        ).json()
        variables = [
            {"name": "BASE_URL", "value": "https://example.test/api"},
            {"name": "AUTH_MODE", "value": "TOKEN"},
            {"name": "AUTH_TOKEN", "value": token, "secret": True},
        ]
        created = client.post("/api/v1/environments", json={
            "project_id": project["id"], "name": "active", "environment_variables": variables,
        })
        secret_ref = created.json()["environment_variables"][2]["secret_ref"]
        activated = client.put(f"/api/v1/environments/{created.json()['id']}", json={
            "name": "active", "status": "ACTIVE", "state_version": 0,
            "environment_variables": [*variables[:2], {
                "name": "AUTH_TOKEN", "secret": True, "secret_ref": secret_ref,
            }],
        })
        body = payload(project["id"])
        body.update({"environment_id": created.json()["id"], "targets": [{
            **body["targets"][0], "url": "health",
        }]})
        definition = client.post("/api/v1/load-tests", json=body).json()
        run = client.post(f"/api/v1/load-tests/{definition['id']}/runs").json()
        for _ in range(100):
            result = client.get(f"/api/v1/load-test-runs/{run['id']}").json()
            if result["status"] != "RUNNING":
                break
            time.sleep(.01)

    assert activated.status_code == 200
    assert token not in created.text and token not in activated.text
    assert result["status"] == "SUCCEEDED"
    assert calls[0]["url"] == "https://example.test/api/health"
    assert calls[0]["headers"]["Authorization"] == f"Bearer {token}"


def test_run_is_accepted_persisted_and_second_active_run_rejected() -> None:
    gate = Event()

    def request(**_):
        gate.wait(2)
        return Response()

    executor = LoadTestExecutor(
        request_callable=request, resolver=lambda _: ["93.184.216.34"]
    )
    app = create_app(load_test_executor=executor)
    client = TestClient(app)
    project = client.post("/api/v1/projects", json={"name": "Runs", "description": ""}).json()
    definition = client.post("/api/v1/load-tests", json=payload(project["id"])).json()
    started = client.post(f"/api/v1/load-tests/{definition['id']}/runs")
    assert started.status_code == 202
    assert started.json()["status"] == "RUNNING"
    assert client.post(f"/api/v1/load-tests/{definition['id']}/runs").status_code == 409
    gate.set()
    run_id = started.json()["id"]
    for _ in range(100):
        result = client.get(f"/api/v1/load-test-runs/{run_id}").json()
        if result["status"] != "RUNNING":
            break
        time.sleep(.01)
    assert result["status"] == "SUCCEEDED"
    assert result["metrics"]["total_requests"] == 1
    listed = client.get(f"/api/v1/load-tests/{definition['id']}/runs").json()
    assert listed["items"][0]["id"] == run_id


def test_running_load_test_can_be_cancelled() -> None:
    gate = Event()

    def request(**_):
        gate.wait(2)
        return Response()

    app = create_app(load_test_executor=LoadTestExecutor(
        request_callable=request, resolver=lambda _: ["93.184.216.34"],
    ))
    client = TestClient(app)
    project = client.post("/api/v1/projects", json={"name": "Cancel", "description": ""}).json()
    body = payload(project["id"])
    body["request_count"] = 10
    definition = client.post("/api/v1/load-tests", json=body).json()
    run = client.post(f"/api/v1/load-tests/{definition['id']}/runs").json()

    cancelled = client.post(f"/api/v1/load-test-runs/{run['id']}/cancel")
    assert cancelled.status_code == 202
    gate.set()
    for _ in range(100):
        result = client.get(f"/api/v1/load-test-runs/{run['id']}").json()
        if result["status"] != "RUNNING":
            break
        time.sleep(.01)
    assert result["status"] == "FAILED"
    assert result["error_message"] == "Load test execution cancelled"


def test_app_startup_recovers_interrupted_runs() -> None:
    repository = LoadTestRepository()
    definition = repository.create_load_test(
        uuid4(), "Interrupted", "", payload(str(uuid4()))["targets"],
        LoadTestMode.COUNT, 1, 1, 1, 0, 5,
    )
    interrupted = repository.create_run(definition.id)

    with TestClient(create_app(load_test_repository=repository)):
        recovered = repository.get_run(interrupted.id)
        assert recovered.status.value == "FAILED"
        assert recovered.finished_at is not None
        assert "service restart" in (recovered.error_message or "")