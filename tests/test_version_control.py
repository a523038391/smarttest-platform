import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from services.api.app import create_app
from services.api.auth_repository import MemoryAuthRepository
from services.api.config import Settings
from services.api.service_control_service import ServiceControlService
from services.api.version_control_service import (
    VersionControlService,
    VersionControlStatus,
    _normalize_windows_proxy,
)


def _git(cwd: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments], cwd=cwd, capture_output=True, text=True,
        check=False, shell=False, timeout=10,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _configure_identity(repository: Path) -> None:
    _git(repository, "config", "user.name", "Version Control Test")
    _git(repository, "config", "user.email", "version-control@example.invalid")


@pytest.fixture
def git_repositories(tmp_path) -> tuple[Path, Path]:
    seed = tmp_path / "seed"
    remote = tmp_path / "remote.git"
    managed = tmp_path / "managed"
    seed.mkdir()
    _git(seed, "init", "-b", "main")
    _configure_identity(seed)
    (seed / "README.md").write_text("initial\n", encoding="utf-8")
    _git(seed, "add", "README.md")
    _git(seed, "commit", "-m", "initial")
    _git(tmp_path, "clone", "--bare", str(seed), str(remote))
    _git(tmp_path, "clone", str(remote), str(managed))
    _configure_identity(managed)
    return managed, remote


def _client(repository: Path) -> TestClient:
    settings = Settings(
        auth_required=False,
        version_control_enabled=True,
        version_control_root=str(repository.resolve()),
    )
    return TestClient(create_app(settings=settings))


def test_settings_and_disabled_endpoints(tmp_path) -> None:
    configured = Settings.from_env({
        "AUTH_REQUIRED": "false",
        "VERSION_CONTROL_ENABLED": "true",
        "VERSION_CONTROL_ROOT": str(tmp_path.resolve()),
    })
    assert configured.version_control_enabled is True
    assert configured.version_control_root == str(tmp_path.resolve())
    with pytest.raises(ValueError, match="VERSION_CONTROL_ROOT"):
        Settings(version_control_enabled=True, version_control_root="relative")

    app = create_app(settings=Settings(auth_required=False))
    client = TestClient(app)
    assert app.state.version_control_service is not None
    assert client.get("/api/v1/version-control/status").json() == {
        "enabled": False,
        "repository_present": False,
        "branch": None,
        "head_short": None,
        "clean": None,
        "change_count": 0,
        "changed_paths": [],
        "remote_configured": False,
        "restart_scheduled": False,
    }
    pull = client.post("/api/v1/version-control/pull")
    assert pull.status_code == 503
    assert pull.headers["content-type"].startswith("application/problem+json")
    assert pull.json()["code"] == "version_control_disabled"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("127.0.0.1:7890", "http://127.0.0.1:7890"),
        ("http=127.0.0.1:7890;https=127.0.0.1:7891", "http://127.0.0.1:7891"),
        ("https://proxy.example:443", "https://proxy.example:443"),
        ("user:password@proxy.example:8080", None),
        ("not a proxy", None),
    ],
)
def test_windows_proxy_normalization_is_safe(raw, expected) -> None:
    assert _normalize_windows_proxy(raw) == expected


def test_status_reports_only_safe_bounded_repository_metadata(git_repositories) -> None:
    managed, remote = git_repositories
    for index in range(105):
        (managed / f"changed-{index:03}.txt").write_text("change\n", encoding="utf-8")
    client = _client(managed)

    status = client.get("/api/v1/version-control/status")

    assert status.status_code == 200
    body = status.json()
    assert body["enabled"] is True
    assert body["repository_present"] is True
    assert body["branch"] == "main"
    assert len(body["head_short"]) == 12
    assert body["clean"] is False
    assert body["change_count"] == 105
    assert len(body["changed_paths"]) == 100
    assert body["remote_configured"] is True
    assert str(remote) not in status.text
    assert "url" not in status.text.lower()


def test_dirty_pull_is_rejected_without_exposing_remote(git_repositories) -> None:
    managed, _ = git_repositories
    (managed / "README.md").write_text("dirty\n", encoding="utf-8")
    _git(managed, "remote", "set-url", "origin", "https://user:secret@example.invalid/repo.git")

    response = _client(managed).post("/api/v1/version-control/pull")

    assert response.status_code == 409
    assert response.json()["code"] == "dirty_working_tree"
    assert "secret" not in response.text
    assert "example.invalid" not in response.text


def test_publish_pull_and_push_existing_local_commit(git_repositories, tmp_path) -> None:
    managed, remote = git_repositories
    client = _client(managed)
    (managed / "published.txt").write_text("published\n", encoding="utf-8")
    (managed / ".env.example").write_text("SAFE_PLACEHOLDER=\n", encoding="utf-8")

    published = client.post(
        "/api/v1/version-control/publish", json={"commit_message": "publish changes"}
    )
    assert published.status_code == 200
    assert published.json()["clean"] is True
    forbidden_input = client.post(
        "/api/v1/version-control/publish",
        json={"commit_message": "message", "path": str(managed)},
    )
    assert forbidden_input.status_code == 422
    assert _git(remote, "rev-parse", "refs/heads/main") == _git(managed, "rev-parse", "HEAD")

    contributor = tmp_path / "contributor"
    _git(tmp_path, "clone", str(remote), str(contributor))
    _configure_identity(contributor)
    (contributor / "upstream.txt").write_text("upstream\n", encoding="utf-8")
    _git(contributor, "add", "upstream.txt")
    _git(contributor, "commit", "-m", "upstream")
    _git(contributor, "push", "origin", "main")

    pulled = client.post("/api/v1/version-control/pull")
    assert pulled.status_code == 200
    assert (managed / "upstream.txt").read_text(encoding="utf-8") == "upstream\n"

    (managed / "local-only.txt").write_text("local\n", encoding="utf-8")
    _git(managed, "add", "local-only.txt")
    _git(managed, "commit", "-m", "local only")
    pushed = client.post(
        "/api/v1/version-control/publish", json={"commit_message": "unused message"}
    )
    assert pushed.status_code == 200
    assert _git(remote, "rev-parse", "refs/heads/main") == _git(managed, "rev-parse", "HEAD")


@pytest.mark.parametrize("filename", [".env", "private.pem", "state.sqlite3", "copy.bak"])
def test_publish_rejects_and_unstages_sensitive_files(git_repositories, filename) -> None:
    managed, remote = git_repositories
    remote_head = _git(remote, "rev-parse", "refs/heads/main")
    (managed / filename).write_text("must not publish\n", encoding="utf-8")

    response = _client(managed).post(
        "/api/v1/version-control/publish", json={"commit_message": "unsafe"}
    )

    assert response.status_code == 422
    assert response.json()["code"] == "sensitive_files_staged"
    assert filename not in response.text
    assert _git(managed, "diff", "--cached", "--name-only") == ""
    assert _git(remote, "rev-parse", "refs/heads/main") == remote_head


def test_create_app_accepts_service_injection_and_auth_protects_route() -> None:
    service = VersionControlService(False, None)
    app = create_app(
        settings=Settings(auth_required=True),
        auth_repository=MemoryAuthRepository(),
        version_control_service=service,
    )
    client = TestClient(app)

    assert app.state.version_control_service is service
    assert client.get("/api/v1/version-control/status").status_code == 401
    setup = client.post("/api/v1/auth/setup", json={
        "username": "admin",
        "display_name": "Administrator",
        "password": "correct horse battery staple",
    })
    assert setup.status_code == 201
    assert client.get("/api/v1/version-control/status").status_code == 200


def test_publish_rejects_multiline_commit_message(git_repositories) -> None:
    managed, _ = git_repositories
    response = _client(managed).post(
        "/api/v1/version-control/publish",
        json={"commit_message": "unsafe\nmessage"},
    )

    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"


class _SuccessfulVersionControlService:
    def _result(self) -> VersionControlStatus:
        return VersionControlStatus(True, True, "main", "abcdef123456", True, 0, (), True)

    def status(self) -> VersionControlStatus:
        return self._result()

    def pull(self) -> VersionControlStatus:
        return self._result()

    def publish(self, commit_message: str) -> VersionControlStatus:
        assert commit_message == "publish"
        return self._result()


@pytest.mark.parametrize(
    ("path", "payload"),
    [
        ("/api/v1/version-control/pull", None),
        ("/api/v1/version-control/publish", {"commit_message": "publish"}),
    ],
)
def test_successful_git_operation_schedules_all_restart(tmp_path, path, payload) -> None:
    now = datetime.now(timezone.utc)
    (tmp_path / "heartbeat.json").write_text(json.dumps({
        "schema_version": 1, "heartbeat_at": now.isoformat(),
    }), encoding="utf-8")
    settings = Settings(
        auth_required=False,
        service_control_enabled=True,
        service_control_root=str(tmp_path.resolve()),
        version_control_auto_restart=True,
    )
    client = TestClient(create_app(
        settings=settings,
        version_control_service=_SuccessfulVersionControlService(),  # type: ignore[arg-type]
        service_control_service=ServiceControlService(
            True,
            str(tmp_path),
            auto_restart_enabled=True,
            clock=lambda: now,
        ),
    ))

    response = client.post(path, json=payload) if payload else client.post(path)

    assert response.status_code == 200
    assert response.json()["restart_scheduled"] is True
    request = json.loads((tmp_path / "restart-request.json").read_text(encoding="utf-8"))
    assert request["target"] == "all"


def test_offline_supervisor_does_not_fail_successful_git_operation(tmp_path) -> None:
    settings = Settings(
        auth_required=False,
        service_control_enabled=True,
        service_control_root=str(tmp_path.resolve()),
        version_control_auto_restart=True,
    )
    client = TestClient(create_app(
        settings=settings,
        version_control_service=_SuccessfulVersionControlService(),  # type: ignore[arg-type]
    ))

    response = client.post(
        "/api/v1/version-control/publish", json={"commit_message": "publish"}
    )

    assert response.status_code == 200
    assert response.json()["restart_scheduled"] is False