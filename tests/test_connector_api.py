import base64

import pytest
from fastapi.testclient import TestClient

from services.api.app import create_app
from services.api.config import Settings
from services.api.connector_client import ConnectorClient
from services.api.connector_repository import ConnectorRepository


class Response:
    def __init__(self, status: int = 200, body: bytes = b"{}") -> None:
        self.status_code = status
        self.content = body
        self.headers: dict[str, str] = {}

    def iter_content(self, chunk_size: int):
        yield self.content

    def close(self) -> None:
        pass


def settings(with_key: bool = True) -> Settings:
    values = {"AUTH_REQUIRED": "false"}
    if with_key:
        values.update({
            "SECRET_ENCRYPTION_KEY": base64.b64encode(b"c" * 32).decode(),
            "SECRET_ENCRYPTION_KEY_ID": "connector-test-key",
        })
    return Settings.from_env(values)


def payload(**changes):
    value = {
        "name": "Global MCP", "description": "shared", "kind": "HTTP",
        "base_url": "https://connector.example.test/health",
        "auth_type": "BEARER", "auth_header": "Authorization",
        "credential": "top-secret-token", "enabled": True, "timeout_seconds": 5,
    }
    value.update(changes)
    return value


def test_global_crud_secret_persistence_and_non_disclosure() -> None:
    calls = []
    connector_client = ConnectorClient(
        request_callable=lambda **kwargs: calls.append(kwargs) or Response(),
        resolver=lambda _: ["93.184.216.34"],
    )
    client = TestClient(create_app(settings=settings(), connector_client=connector_client))

    created_response = client.post("/api/v1/connectors", json=payload())
    assert created_response.status_code == 201
    created = created_response.json()
    assert created["credential_configured"] is True
    assert "credential" not in created and "project_id" not in created
    assert "top-secret-token" not in created_response.text

    listed = client.get("/api/v1/connectors").json()
    assert listed["total"] == 1 and listed["items"][0]["id"] == created["id"]
    updated_body = payload(description="updated", state_version=created["state_version"])
    del updated_body["credential"]
    updated_response = client.put(
        f"/api/v1/connectors/{created['id']}", json=updated_body,
    )
    assert updated_response.status_code == 200
    updated = updated_response.json()
    assert updated["credential_configured"] is True
    assert "top-secret-token" not in updated_response.text

    tested = client.post(f"/api/v1/connectors/{created['id']}/test")
    assert tested.status_code == 200 and tested.json()["success"] is True
    assert calls[0]["headers"]["Authorization"] == "Bearer top-secret-token"
    assert calls[0]["allow_redirects"] is False
    assert "top-secret-token" not in tested.text

    cleared_body = payload(
        description="cleared", auth_type="NONE",
        state_version=tested.json()["connector"]["state_version"],
    )
    del cleared_body["credential"]
    cleared = client.put(
        f"/api/v1/connectors/{created['id']}", json=cleared_body,
    ).json()
    assert cleared["credential_configured"] is False

    assert client.delete(f"/api/v1/connectors/{created['id']}").status_code == 204
    assert client.get("/api/v1/connectors").json()["total"] == 0


def test_toggle_uses_optimistic_version_and_names_are_globally_unique() -> None:
    client = TestClient(create_app(settings=settings()))
    created = client.post("/api/v1/connectors", json=payload(
        auth_type="NONE", credential=None,
    )).json()
    duplicate = client.post("/api/v1/connectors", json=payload(
        auth_type="NONE", credential=None,
    ))
    assert duplicate.status_code == 409
    assert duplicate.headers["content-type"].startswith("application/problem+json")

    toggled = client.patch(
        f"/api/v1/connectors/{created['id']}/enabled",
        json={"enabled": False, "state_version": 0},
    )
    assert toggled.status_code == 200 and toggled.json()["enabled"] is False
    conflict = client.patch(
        f"/api/v1/connectors/{created['id']}/enabled",
        json={"enabled": True, "state_version": 0},
    )
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "connector_version_conflict"


def test_authenticated_update_without_credential_returns_422() -> None:
    client = TestClient(create_app(settings=settings()))
    created = client.post("/api/v1/connectors", json=payload(
        auth_type="NONE", credential=None,
    )).json()
    update = payload(
        auth_type="BEARER", credential=None, state_version=created["state_version"],
    )
    del update["credential"]

    response = client.put(f"/api/v1/connectors/{created['id']}", json=update)

    assert response.status_code == 422
    assert response.json()["code"] == "connector_credential_required"
    assert "top-secret-token" not in response.text


def test_in_flight_config_change_returns_outcome_without_saving_stale_result() -> None:
    repository = ConnectorRepository()
    changed = False

    def request(**_):
        nonlocal changed
        if not changed:
            current = repository.list_connectors()[0]
            repository.update_connector(
                current.id, expected_version=current.state_version,
                name=current.name, description="changed concurrently", kind=current.kind,
                base_url=current.base_url, auth_type=current.auth_type,
                auth_header=current.auth_header, credential=None,
                credential_provided=False, enabled=current.enabled,
                timeout_seconds=current.timeout_seconds,
            )
            changed = True
        return Response()

    connector_client = ConnectorClient(
        request_callable=request, resolver=lambda _: ["93.184.216.34"],
    )
    client = TestClient(create_app(
        settings=settings(), connector_repository=repository,
        connector_client=connector_client,
    ))
    created = client.post("/api/v1/connectors", json=payload(
        auth_type="NONE", credential=None,
    )).json()

    response = client.post(f"/api/v1/connectors/{created['id']}/test")

    assert response.status_code == 200
    result = response.json()
    assert result["success"] is True
    assert "not saved" in result["message"]
    assert result["connector"]["description"] == "changed concurrently"
    assert result["connector"]["state_version"] == 1
    assert result["connector"]["last_tested_at"] is None
    assert result["connector"]["last_test_success"] is None
    assert result["connector"]["tools"] == []


def test_connector_private_host_allowlist_accepts_hostnames_only() -> None:
    configured = Settings.from_env({
        "CONNECTOR_PRIVATE_HOST_ALLOWLIST": " MCP.INTERNAL,connector.internal ",
    })
    assert configured.connector_private_host_allowlist == (
        "mcp.internal", "connector.internal",
    )
    for literal in ("192.168.1.10", "2001:db8::1", "3232235777"):
        with pytest.raises(ValueError, match="CONNECTOR_PRIVATE_HOST_ALLOWLIST"):
            Settings(connector_private_host_allowlist=(literal,))


def test_secret_write_fails_closed_without_encryption_configuration() -> None:
    response = TestClient(create_app(settings=settings(False))).post(
        "/api/v1/connectors", json=payload(),
    )
    assert response.status_code == 503
    assert response.json()["code"] == "secret_encryption_unavailable"
    assert "top-secret-token" not in response.text