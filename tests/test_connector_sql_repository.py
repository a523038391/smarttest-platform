import pytest

from services.api.connector_domain import ConnectorAuthType, ConnectorKind
from services.api.connector_models import ConnectorModel
from services.api.connector_repository import ConnectorNameConflict
from services.api.connector_sql_repository import SqlConnectorRepository
from services.api.database import Base, create_database_engine, create_session_factory
from services.api.environment_crypto import SecretEncryptor


def test_connector_and_encrypted_credential_are_persisted(tmp_path) -> None:
    engine = create_database_engine(
        f"sqlite+pysqlite:///{(tmp_path / 'connectors.sqlite3').as_posix()}"
    )
    Base.metadata.create_all(engine)
    sessions = create_session_factory(engine)
    encryptor = SecretEncryptor(b"s" * 32, "sql-connector-key")
    repository = SqlConnectorRepository(sessions, encryptor)
    created = repository.create_connector(
        name="SQL connector", description="", kind=ConnectorKind.HTTP,
        base_url="https://example.test", auth_type=ConnectorAuthType.API_KEY,
        auth_header="X-API-Key", credential="database-secret",
        enabled=True, timeout_seconds=10,
    )

    reloaded = SqlConnectorRepository(sessions, encryptor).get_connector(created.id)
    with sessions() as session:
        stored = session.get(ConnectorModel, str(created.id))
        assert stored is not None
        assert stored.ciphertext != b"database-secret"
        assert stored.key_id == "sql-connector-key"
    assert reloaded == created
    assert repository.credential_for(reloaded) == "database-secret"
    engine.dispose()


def test_connector_names_are_ascii_case_insensitive_on_create_and_update(tmp_path) -> None:
    engine = create_database_engine(
        f"sqlite+pysqlite:///{(tmp_path / 'connector-names.sqlite3').as_posix()}"
    )
    Base.metadata.create_all(engine)
    repository = SqlConnectorRepository(
        create_session_factory(engine), SecretEncryptor(b"s" * 32, "sql-connector-key"),
    )
    common = {
        "description": "", "kind": ConnectorKind.HTTP,
        "base_url": "https://example.test", "auth_type": ConnectorAuthType.NONE,
        "auth_header": "Authorization", "credential": None,
        "enabled": True, "timeout_seconds": 10,
    }
    try:
        first = repository.create_connector(name="SQL Connector", **common)
        with pytest.raises(ConnectorNameConflict):
            repository.create_connector(name="sql connector", **common)
        second = repository.create_connector(name="Other", **common)
        with pytest.raises(ConnectorNameConflict):
            repository.update_connector(
                second.id, expected_version=second.state_version,
                name="sQl cOnNeCtOr", credential_provided=False, **common,
            )
        assert repository.get_connector(first.id).name == "SQL Connector"
        assert repository.get_connector(second.id).name == "Other"
    finally:
        engine.dispose()