from uuid import uuid4

from fastapi.testclient import TestClient

from packages.protocol import Engine, EventEnvelope, EventType
from services.api.app import create_app
from services.api.domain import InvalidTransition, RunState


def test_health_endpoints() -> None:
    client = TestClient(create_app())
    assert client.get("/health/live").json() == {"status": "ok"}
    assert client.get("/health/ready").json() == {"status": "ok"}


def test_create_list_get_and_idempotency() -> None:
    client = TestClient(create_app())
    headers = {"Idempotency-Key": "request-1"}
    payload = {"engine": "pytest", "parameters": {"suite": "smoke"}}

    first = client.post("/api/v1/runs", json=payload, headers=headers)
    second = client.post("/api/v1/runs", json=payload, headers=headers)

    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json()["id"] == first.json()["id"]
    conflict = client.post(
        "/api/v1/runs",
        json={"engine": "http"},
        headers=headers,
    )
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "idempotency_conflict"
    run_id = first.json()["id"]
    assert client.get(f"/api/v1/runs/{run_id}").json()["state"] == "CREATED"
    listed = client.get("/api/v1/runs").json()
    assert listed["total"] == 1
    assert listed["items"][0]["id"] == run_id


def test_cancel_created_and_running_runs() -> None:
    app = create_app()
    client = TestClient(app)
    created = client.post("/api/v1/runs", json={"engine": "http"}).json()
    assert client.post(f"/api/v1/runs/{created['id']}/cancel").json()["state"] == "CANCELLING"

    running = app.state.run_repository.create(Engine.PLAYWRIGHT, {})
    for state in (RunState.QUEUED, RunState.DISPATCHING, RunState.RUNNING):
        app.state.run_repository.transition(running.id, state)
    response = client.post(f"/api/v1/runs/{running.id}/cancel")
    assert response.json()["state"] == "CANCELLING"


def test_attempt_event_listing_and_terminal_sse_resume() -> None:
    app = create_app()
    repository = app.state.run_repository
    run = repository.create(Engine.HTTP, {})
    attempt = repository.create_attempt(run.id)
    first = repository.append_event(EventEnvelope(
        run_id=run.id,
        attempt_id=attempt.id,
        engine=run.engine,
        event_id=uuid4(),
        seq=0,
        type=EventType.STARTED,
    ))
    repository.transition(run.id, RunState.QUEUED)
    repository.transition(run.id, RunState.FAILED)
    client = TestClient(app)

    assert client.get(f"/api/v1/runs/{run.id}/attempts").json()["total"] == 1
    assert client.get(f"/api/v1/runs/{run.id}/events").json()["items"][0]["cursor"] == first.cursor
    stream = client.get(f"/api/v1/runs/{run.id}/events/stream")
    assert f"id: {first.cursor}\n" in stream.text
    resumed = client.get(
        f"/api/v1/runs/{run.id}/events/stream",
        headers={"Last-Event-ID": str(first.cursor)},
    )
    assert resumed.text == ""


def test_problem_responses_for_validation_missing_and_conflict() -> None:
    app = create_app()
    client = TestClient(app)
    invalid = client.post("/api/v1/runs", json={"engine": "unknown", "extra": True})
    assert invalid.status_code == 422
    assert invalid.headers["content-type"].startswith("application/problem+json")
    invalid_problem = invalid.json()
    assert invalid_problem["code"] == "validation_error"
    assert invalid_problem["errors"]
    assert all(set(error) == {"loc", "message", "type"} for error in invalid_problem["errors"])
    assert "unknown" not in invalid.text

    missing = client.get(f"/api/v1/runs/{uuid4()}")
    assert missing.status_code == 404
    assert missing.json()["code"] == "run_not_found"

    run = app.state.run_repository.create(Engine.HTTP, {})
    app.state.run_repository.cancel(run.id)
    app.state.run_repository.transition(run.id, RunState.CANCELLED)
    conflict = client.post(f"/api/v1/runs/{run.id}/cancel")
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "terminal_run_conflict"


def test_blank_idempotency_header_is_field_validation_error() -> None:
    client = TestClient(create_app())
    response = client.post(
        "/api/v1/runs",
        json={"engine": "http"},
        headers={"Idempotency-Key": "   "},
    )

    assert response.status_code == 422
    problem = response.json()
    assert problem["code"] == "validation_error"
    assert problem["errors"] == [
        {
            "loc": ["header", "Idempotency-Key"],
            "message": "Idempotency-Key must not be blank",
            "type": "value_error",
        }
    ]
    assert "input" not in response.text


def test_invalid_transition_handler_with_stub_repository() -> None:
    class StubRepository:
        def cancel(self, _run_id: object) -> None:
            raise InvalidTransition("stub transition rejected")

    app = create_app()
    app.state.run_repository = StubRepository()
    response = TestClient(app).post(f"/api/v1/runs/{uuid4()}/cancel")

    assert response.status_code == 409
    assert response.json()["code"] == "invalid_transition"


def test_attempt_event_list_and_terminal_sse_resume() -> None:
    app = create_app()
    client = TestClient(app)
    repository = app.state.run_repository
    run = repository.create(Engine.PYTEST, {})
    attempt = repository.create_attempt(run.id)
    event = repository.append_event(EventEnvelope(
        run_id=run.id,
        attempt_id=attempt.id,
        engine=run.engine,
        event_id=uuid4(),
        seq=0,
        type=EventType.LOG,
        payload={"message": "hello"},
    ))
    repository.cancel(run.id)
    repository.transition(run.id, RunState.CANCELLED)

    attempts = client.get(f"/api/v1/runs/{run.id}/attempts").json()
    assert attempts["items"][0]["outcome"] is None
    events = client.get(f"/api/v1/runs/{run.id}/events").json()
    assert events["next_cursor"] == event.cursor
    assert events["items"][0]["payload"] == {"message": "hello"}

    stream = client.get(f"/api/v1/runs/{run.id}/events/stream")
    assert stream.status_code == 200
    assert f"id: {event.cursor}" in stream.text
    resumed = client.get(
        f"/api/v1/runs/{run.id}/events/stream",
        headers={"Last-Event-ID": str(event.cursor)},
    )
    assert resumed.text == ""