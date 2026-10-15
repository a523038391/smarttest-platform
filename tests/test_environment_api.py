import base64
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from services.api.app import create_app
from services.api.config import Settings
from services.api.load_test_auth import LoadTestAuthMode
from services.api.load_test_executor import LoadTestExecutionError


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


class _AuthTestExecutor:
    def __init__(self, *, fail: bool = False, failure_code: str = "execution_error") -> None:
        self.fail = fail
        self.failure_code = failure_code
        self.observed_mode = None
        self.observed_login_body = None

    def test_authentication(self, config, timeout: float = 10):
        self.observed_mode = config.mode
        self.observed_login_body = config.login_body
        if self.fail:
            raise LoadTestExecutionError("authentication failed", code=self.failure_code)
        return config.mode


@pytest.mark.parametrize("fail", [False, True])
def test_environment_authentication_can_be_tested_without_returning_secrets(
    fail: bool,
) -> None:
    executor = _AuthTestExecutor(fail=fail)
    client = TestClient(create_app(settings=settings(), load_test_executor=executor))
    account, password = "test-account", "test-password"
    created = client.post("/api/v1/environments", json={
        "project_id": str(uuid4()), "name": "auth-test",
        "environment_variables": [
            {"name": "BASE_URL", "value": "https://example.test"},
            {"name": "AUTH_MODE", "value": "LOGIN"},
            {"name": "LOGIN_URL", "value": "/session"},
            {"name": "ACCOUNT_FIELD", "value": "username"},
            {"name": "PASSWORD_FIELD", "value": "password"},
            {"name": "LOGIN_BODY", "value": "{\"tenantName\":\"demo\"}"},
            {"name": "TOKEN_PATH", "value": "data.token"},
            {"name": "AUTH_HEADER", "value": "Authorization"},
            {"name": "AUTH_PREFIX", "value": "Bearer"},
            {"name": "AUTH_ACCOUNT", "value": account, "secret": True},
            {"name": "AUTH_PASSWORD", "value": password, "secret": True},
        ],
    }).json()
    writes = [
        ({"name": item["name"], "secret": True, "secret_ref": item["secret_ref"]}
         if item["secret"] else {"name": item["name"], "value": item["value"]})
        for item in created["environment_variables"]
    ]
    activated = client.put(f"/api/v1/environments/{created['id']}", json={
        "name": created["name"], "status": "ACTIVE", "state_version": 0,
        "environment_variables": writes, "common_parameters": [],
    })

    response = client.post(f"/api/v1/environments/{created['id']}/test-auth")

    assert activated.status_code == 200
    assert response.status_code == 200
    assert response.json()["success"] is not fail
    assert response.json()["mode"] == "LOGIN"
    assert executor.observed_mode is LoadTestAuthMode.LOGIN
    assert executor.observed_login_body == {"tenantName": "demo"}
    assert account not in response.text
    assert password not in response.text


@pytest.mark.parametrize(("code", "message"), [
    ("login_response_invalid", "响应不是有效 JSON"),
    ("login_token_path_missing", "不存在配置的 Token 路径"),
    ("login_token_invalid", "值不是非空字符串"),
    ("login_request_failed", "登录接口请求失败"),
])
def test_environment_authentication_returns_safe_failure_reason(
    code: str, message: str,
) -> None:
    executor = _AuthTestExecutor(fail=True, failure_code=code)
    client = TestClient(create_app(settings=settings(), load_test_executor=executor))
    created = client.post("/api/v1/environments", json={
        "project_id": str(uuid4()), "name": "safe-failure",
        "environment_variables": [
            {"name": "BASE_URL", "value": "https://example.test"},
            {"name": "AUTH_MODE", "value": "LOGIN"},
            {"name": "LOGIN_URL", "value": "/session"},
            {"name": "AUTH_ACCOUNT", "value": "account", "secret": True},
            {"name": "AUTH_PASSWORD", "value": "password", "secret": True},
        ],
    }).json()
    writes = [
        ({"name": item["name"], "secret": True, "secret_ref": item["secret_ref"]}
         if item["secret"] else {"name": item["name"], "value": item["value"]})
        for item in created["environment_variables"]
    ]
    activated = client.put(f"/api/v1/environments/{created['id']}", json={
        "name": created["name"], "status": "ACTIVE", "state_version": 0,
        "environment_variables": writes, "common_parameters": [],
    })

    response = client.post(f"/api/v1/environments/{created['id']}/test-auth")

    assert activated.status_code == 200
    assert response.status_code == 200
    assert response.json()["success"] is False
    assert message in response.json()["message"]


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