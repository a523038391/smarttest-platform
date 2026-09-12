import json
import os
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from scripts.platform_supervisor import PlatformSupervisor, parse_control_request
from services.api.app import create_app
from services.api.auth_repository import MemoryAuthRepository
from services.api.config import Settings
from services.api.service_control_service import (
    ServiceControlService,
    ServiceControlUnavailable,
)


def _heartbeat(root, timestamp: datetime) -> None:
    root.mkdir(exist_ok=True)
    (root / "heartbeat.json").write_text(json.dumps({
        "schema_version": 1,
        "heartbeat_at": timestamp.isoformat(),
    }), encoding="utf-8")


def test_service_control_settings_defaults_environment_and_validation(tmp_path) -> None:
    assert Settings().service_control_enabled is False
    assert Settings().version_control_auto_restart is False
    configured = Settings.from_env({
        "SERVICE_CONTROL_ENABLED": "true",
        "SERVICE_CONTROL_ROOT": str(tmp_path.resolve()),
        "VERSION_CONTROL_AUTO_RESTART": "true",
    })
    assert configured.service_control_enabled is True
    assert configured.service_control_root == str(tmp_path.resolve())
    assert configured.version_control_auto_restart is True
    with pytest.raises(ValueError, match="SERVICE_CONTROL_ROOT"):
        Settings(service_control_enabled=True, service_control_root="relative")
    with pytest.raises(ValueError, match="VERSION_CONTROL_AUTO_RESTART"):
        Settings(version_control_auto_restart=True)


def test_supervisor_uses_fixed_frontend_port(tmp_path) -> None:
    supervisor = PlatformSupervisor(tmp_path)
    assert "--strictPort" in supervisor._commands["frontend"]


def test_fresh_and_expired_heartbeat_are_bounded_and_type_safe(tmp_path) -> None:
    now = datetime(2026, 9, 12, 12, 0, tzinfo=timezone.utc)
    current = [now]
    _heartbeat(tmp_path, now)
    service = ServiceControlService(
        True, str(tmp_path), clock=lambda: current[0]
    )

    assert service.status().supervisor_online is True
    current[0] = now + timedelta(seconds=5)
    assert service.status().supervisor_online is True
    current[0] = now + timedelta(seconds=5, microseconds=1)
    assert service.status().supervisor_online is False

    (tmp_path / "heartbeat.json").write_text(
        '{"schema_version":true,"heartbeat_at":"2026-09-12T12:00:00+00:00"}',
        encoding="utf-8",
    )
    current[0] = now
    assert service.status().supervisor_online is False
    (tmp_path / "heartbeat.json").write_bytes(b"x" * 4097)
    assert service.status().supervisor_online is False


def test_restart_request_is_atomic_strict_and_delayed(tmp_path, monkeypatch) -> None:
    now = datetime(2026, 9, 12, 12, 0, tzinfo=timezone.utc)
    _heartbeat(tmp_path, now)
    destination = tmp_path / "restart-request.json"
    destination.write_text('{"old":true}', encoding="utf-8")
    replacements: list[tuple[object, object]] = []
    real_replace = os.replace

    def recording_replace(source, target) -> None:
        replacements.append((source, target))
        real_replace(source, target)

    monkeypatch.setattr("services.api.service_control_service.os.replace", recording_replace)
    service = ServiceControlService(True, str(tmp_path), clock=lambda: now)

    request = service.request_restart("backend")
    payload = json.loads(destination.read_text(encoding="utf-8"))

    assert replacements and replacements[-1][1] == destination
    assert set(payload) == {
        "schema_version", "target", "request_id", "requested_at", "not_before",
    }
    assert payload["schema_version"] == 1
    assert payload["target"] == "backend"
    assert datetime.fromisoformat(payload["not_before"]) - datetime.fromisoformat(
        payload["requested_at"]
    ) == timedelta(seconds=2)
    assert str(request.request_id) == payload["request_id"]
    assert parse_control_request(destination) is not None
    assert not list(tmp_path.glob("*.tmp"))


def test_offline_restart_raises_domain_exception(tmp_path) -> None:
    service = ServiceControlService(True, str(tmp_path))
    with pytest.raises(ServiceControlUnavailable):
        service.request_restart("all")


def test_service_control_api_auth_compatibility_and_offline_problem(tmp_path) -> None:
    protected = create_app(
        settings=Settings(auth_required=True),
        auth_repository=MemoryAuthRepository(),
    )
    protected_client = TestClient(protected)
    assert protected_client.get("/api/v1/service-control/status").status_code == 401
    setup = protected_client.post("/api/v1/auth/setup", json={
        "username": "admin",
        "display_name": "Administrator",
        "password": "correct horse battery staple",
    })
    assert setup.status_code == 201
    assert protected_client.get("/api/v1/service-control/status").status_code == 200

    settings = Settings(
        auth_required=False,
        service_control_enabled=True,
        service_control_root=str(tmp_path.resolve()),
    )
    injected_service = ServiceControlService(True, str(tmp_path))
    compatible = create_app(
        settings=settings, service_control_service=injected_service
    )
    assert compatible.state.service_control_service is injected_service
    client = TestClient(compatible)
    assert client.get("/api/v1/service-control/status").json() == {
        "enabled": True,
        "supervisor_online": False,
        "auto_restart_enabled": False,
    }
    offline = client.post("/api/v1/service-control/restart", json={"target": "all"})
    assert offline.status_code == 503
    assert offline.headers["content-type"].startswith("application/problem+json")
    assert offline.json()["code"] == "service_control_unavailable"


def test_restart_api_accepts_only_fixed_target_without_control_fields(tmp_path) -> None:
    now = datetime.now(timezone.utc)
    _heartbeat(tmp_path, now)
    client = TestClient(create_app(settings=Settings(
        auth_required=False,
        service_control_enabled=True,
        service_control_root=str(tmp_path.resolve()),
    )))

    response = client.post(
        "/api/v1/service-control/restart", json={"target": "frontend"}
    )
    assert response.status_code == 202
    assert response.json() == {
        "accepted": True, "target": "frontend", "reconnect_after_seconds": 2,
    }
    assert client.post(
        "/api/v1/service-control/restart",
        json={"target": "all", "command": "anything", "pid": 1, "path": "C:\\"},
    ).status_code == 422
    assert client.post(
        "/api/v1/service-control/restart", json={"target": "worker"}
    ).status_code == 422