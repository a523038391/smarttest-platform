import base64
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from services.api.app import create_app
from services.api.config import Settings


def settings() -> Settings:
    return Settings.from_env({
        "AUTH_REQUIRED": "false",
        "SECRET_ENCRYPTION_KEY": base64.b64encode(b"a" * 32).decode(),
        "SECRET_ENCRYPTION_KEY_ID": "api-key",
    })


def test_environment_api_redacts_secrets_and_reports_conflicts() -> None:
    client = TestClient(create_app(settings=settings()))
    project_id = str(uuid4())
    payload = {
        "project_id": project_id, "name": "staging",
        "environment_variables": [
            {"name": "URL", "value": "https://example.test"},
            {"name": "TOKEN", "value": "top-secret", "secret": True},
        ],
        "common_parameters": [{"name": "retry", "value": {"count": 2}}],
    }
    created = client.post("/api/v1/environments", json=payload)
    body = created.json()
    secret = body["environment_variables"][1]

    assert created.status_code == 201
    assert secret["configured"] is True
    assert "value" not in secret
    assert "top-secret" not in created.text
    assert "ciphertext" not in created.text
    update = {
        "name": "staging", "status": "ACTIVE", "state_version": 0,
        "environment_variables": [
            {"name": "URL", "value": "https://example.test"},
            {"name": "TOKEN", "secret": True, "secret_ref": secret["secret_ref"]},
        ],
        "common_parameters": payload["common_parameters"],
    }
    updated = client.put(f"/api/v1/environments/{body['id']}", json=update)
    revisions = client.get(f"/api/v1/environments/{body['id']}/revisions")
    stale = client.put(f"/api/v1/environments/{body['id']}", json=update)
    assert updated.status_code == 200
    assert updated.json()["revision"] == 2
    assert revisions.json()["total"] == 2
    assert stale.status_code == 409
    assert stale.json()["code"] == "environment_version_conflict"
    duplicate = client.post("/api/v1/environments", json=payload)
    assert duplicate.status_code == 409
    assert duplicate.json()["code"] == "environment_conflict"
    assert client.get("/api/v1/environments").status_code == 422
    assert client.get(f"/api/v1/environments/{uuid4()}").status_code == 404


def test_environment_api_fails_closed_only_for_secret_writes() -> None:
    client = TestClient(create_app(settings=Settings.from_env({"AUTH_REQUIRED": "false"})))
    public = client.post("/api/v1/environments", json={
        "project_id": str(uuid4()), "name": "public",
        "environment_variables": [{"name": "URL", "value": "local"}],
    })
    secret = client.post("/api/v1/environments", json={
        "project_id": str(uuid4()), "name": "secret",
        "environment_variables": [
            {"name": "TOKEN", "value": "hidden", "secret": True}
        ],
    })

    assert public.status_code == 201
    assert secret.status_code == 503
    assert secret.json()["code"] == "secret_encryption_unavailable"


def test_secret_settings_are_repr_safe() -> None:
    configured = settings()
    assert configured.secret_encryption_key == b"a" * 32
    assert "api-key" not in repr(configured)
    assert base64.b64encode(b"a" * 32).decode() not in repr(configured)


@pytest.mark.parametrize(
    "environment",
    [
        {"SECRET_ENCRYPTION_KEY": base64.b64encode(b"a" * 32).decode()},
        {"SECRET_ENCRYPTION_KEY_ID": "api-key"},
    ],
)
def test_secret_settings_reject_partial_configuration(environment: dict[str, str]) -> None:
    with pytest.raises(ValueError, match="must both be set"):
        Settings.from_env(environment)