import pytest


@pytest.fixture(autouse=True)
def disable_authentication_for_legacy_tests(monkeypatch) -> None:
    monkeypatch.setenv("AUTH_REQUIRED", "false")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("AUTO_DISPATCH", raising=False)