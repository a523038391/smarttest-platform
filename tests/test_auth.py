from collections.abc import Iterator
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import BINARY, Engine, select

from services.api.app import create_app
from services.api.auth_domain import (
    PASSWORD_ITERATIONS, hash_password, new_session_token, session_token_digest,
)
from services.api.auth_models import AuthBootstrapModel, UserSessionModel
from services.api.auth_repository import AuthRepositoryLike, MemoryAuthRepository
from services.api.auth_schemas import LoginRequest, SetupRequest
from services.api.auth_sql_repository import SqlAuthRepository
from services.api.config import Settings
from services.api.database import Base, create_database_engine, create_session_factory


PASSWORD = "correct horse battery staple"
SETUP = {"username": "Admin.User", "display_name": "Administrator", "password": PASSWORD}


def test_auth_mysql_key_columns_use_compatible_types() -> None:
    column = AuthBootstrapModel.__table__.c.singleton_key

    assert column.autoincrement is False
    assert isinstance(UserSessionModel.__table__.c.token_digest.type, BINARY)


@pytest.fixture(params=["memory", "sql"])
def auth_repository(request, tmp_path) -> Iterator[tuple[AuthRepositoryLike, Engine | None]]:
    if request.param == "memory":
        yield MemoryAuthRepository(), None
        return
    engine = create_database_engine(
        f"sqlite+pysqlite:///{(tmp_path / 'auth.sqlite3').as_posix()}"
    )
    Base.metadata.create_all(engine)
    yield SqlAuthRepository(create_session_factory(engine)), engine
    engine.dispose()


def _client(repository: AuthRepositoryLike, *, secure: bool = False) -> TestClient:
    settings = Settings(
        auth_required=True, session_cookie_secure=secure, session_ttl_seconds=3600
    )
    return TestClient(
        create_app(settings=settings, auth_repository=repository),
        base_url="https://testserver" if secure else "http://testserver",
    )


def test_setup_login_me_logout_and_protection_for_memory_and_sql(
    auth_repository,
) -> None:
    repository, _ = auth_repository
    client = _client(repository)

    assert client.get("/api/v1/auth/status").json() == {
        "setup_required": True, "auth_required": True,
        "authenticated": False, "user": None,
    }
    setup = client.post("/api/v1/auth/setup", json=SETUP)
    assert setup.status_code == 201
    assert setup.json()["username"] == "admin.user"
    assert setup.json()["role"] == "ADMIN"
    assert "token" not in setup.text.lower()
    assert client.get("/api/v1/auth/me").json()["display_name"] == "Administrator"
    status_response = client.get("/api/v1/auth/status").json()
    assert status_response["authenticated"] is True
    assert status_response["user"]["username"] == "admin.user"
    assert client.get("/api/v1/runs").status_code == 200

    assert client.post("/api/v1/auth/logout").status_code == 204
    assert client.get("/api/v1/auth/me").status_code == 401
    wrong = client.post("/api/v1/auth/login", json={
        "username": "admin.user", "password": "incorrect password value",
    })
    missing = client.post("/api/v1/auth/login", json={
        "username": "missing.user", "password": "incorrect password value",
    })
    assert wrong.status_code == missing.status_code == 401
    assert wrong.json() == missing.json()

    login = client.post("/api/v1/auth/login", json={
        "username": "ADMIN.USER", "password": PASSWORD,
    })
    assert login.status_code == 200
    assert "token" not in login.text.lower()
    assert client.post("/api/v1/auth/logout").status_code == 204


def test_setup_is_once_only_and_cookie_is_hardened(auth_repository) -> None:
    repository, _ = auth_repository
    client = _client(repository, secure=True)
    first = client.post("/api/v1/auth/setup", json=SETUP)
    cookie = first.headers["set-cookie"].lower()

    assert "smarttest_session=" in cookie
    assert "httponly" in cookie
    assert "samesite=lax" in cookie
    assert "path=/" in cookie
    assert "secure" in cookie
    assert "max-age=3600" in cookie
    second = client.post("/api/v1/auth/setup", json={
        **SETUP, "username": "other.admin",
    })
    assert second.status_code == 409
    assert second.json()["code"] == "auth_already_initialized"


def test_missing_invalid_and_expired_sessions_are_rejected(auth_repository) -> None:
    repository, _ = auth_repository
    client = _client(repository)
    missing = client.get("/api/v1/runs")
    client.cookies.set("smarttest_session", "not-a-valid-session")
    invalid = client.get("/api/v1/runs")
    assert missing.status_code == invalid.status_code == 401
    assert missing.json() == invalid.json()
    assert missing.json()["code"] == "authentication_required"

    user = repository.initialize_admin("admin", "Admin", hash_password(PASSWORD))
    token = new_session_token()
    repository.create_session(
        user.id, session_token_digest(token),
        datetime.now(timezone.utc) - timedelta(seconds=1),
    )
    client.cookies.set("smarttest_session", token)
    assert client.get("/api/v1/auth/me").status_code == 401


def test_sql_stores_only_session_digest(auth_repository) -> None:
    repository, engine = auth_repository
    if engine is None:
        pytest.skip("SQL-specific storage assertion")
    client = _client(repository)
    response = client.post("/api/v1/auth/setup", json=SETUP)
    raw_token = client.cookies.get("smarttest_session")
    with create_session_factory(engine)() as session:
        stored = session.scalar(select(UserSessionModel))
    assert response.status_code == 201
    assert stored is not None
    assert len(stored.token_digest) == 32
    assert stored.token_digest == session_token_digest(raw_token)
    assert raw_token.encode() != stored.token_digest


def test_password_repr_and_hash_policy() -> None:
    setup = SetupRequest(**SETUP)
    login = LoginRequest(username="admin", password=PASSWORD)
    password = hash_password(PASSWORD)
    assert PASSWORD not in repr(setup)
    assert PASSWORD not in repr(login)
    assert PASSWORD_ITERATIONS >= 600_000
    assert len(password.salt) == 32
    assert len(password.digest) == 32


def test_auth_settings_environment_and_validation() -> None:
    configured = Settings.from_env({
        "AUTH_REQUIRED": "false", "SESSION_COOKIE_SECURE": "true",
        "SESSION_TTL_SECONDS": "7200",
    })
    assert configured.auth_required is False
    assert configured.session_cookie_secure is True
    assert configured.session_ttl_seconds == 7200
    with pytest.raises(ValueError, match="SESSION_TTL_SECONDS"):
        Settings(session_ttl_seconds=0)


def test_public_routes_and_options_are_exempt() -> None:
    client = _client(MemoryAuthRepository())
    assert client.get("/health/live").status_code == 200
    assert client.get("/openapi.json").status_code == 200
    assert client.get("/docs").status_code == 200
    assert client.options("/api/v1/runs").status_code != 401


def test_repeated_failed_logins_are_rate_limited() -> None:
    client = _client(MemoryAuthRepository())
    assert client.post("/api/v1/auth/setup", json=SETUP).status_code == 201
    client.post("/api/v1/auth/logout")
    payload = {"username": "admin.user", "password": "incorrect password value"}
    assert [client.post("/api/v1/auth/login", json=payload).status_code for _ in range(5)] == [
        401, 401, 401, 401, 401,
    ]
    limited = client.post("/api/v1/auth/login", json=payload)
    assert limited.status_code == 429
    assert limited.json()["code"] == "login_rate_limited"