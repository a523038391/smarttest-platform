from uuid import uuid4

from services.api.database import Base, create_database_engine, create_session_factory
from services.api.environment_crypto import SecretEncryptor
from services.api.environment_domain import ConfigurationValueInput, EnvironmentStatus
from services.api.environment_models import EnvironmentValueModel
from services.api.environment_sql_repository import SqlEnvironmentRepository


def test_environment_and_encrypted_revisions_are_persisted(tmp_path) -> None:
    engine = create_database_engine(
        f"sqlite+pysqlite:///{(tmp_path / 'environments.sqlite3').as_posix()}"
    )
    Base.metadata.create_all(engine)
    sessions = create_session_factory(engine)
    encryptor = SecretEncryptor(b"s" * 32, "sql-key")
    repository = SqlEnvironmentRepository(sessions, encryptor)
    created = repository.create_environment(
        uuid4(), "sql", [ConfigurationValueInput("TOKEN", "secret", secret=True)], []
    )
    secret = created.values[0]
    active = repository.update_environment(
        created.id, expected_version=0, name="sql", status=EnvironmentStatus.ACTIVE,
        environment_variables=[ConfigurationValueInput(
            "TOKEN", secret=True, secret_ref=secret.secret_ref
        )], common_parameters=[],
    )

    reloaded = SqlEnvironmentRepository(sessions, encryptor).get_environment(created.id)
    with sessions() as session:
        stored = session.get(
            EnvironmentValueModel,
            (str(created.id), 1, "environment_variable", "TOKEN"),
        )
        assert stored is not None
        assert stored.public_value is None
        assert stored.ciphertext != b"secret"
        assert stored.key_id == "sql-key"
    assert reloaded == active
    assert len(repository.list_revisions(created.id)) == 2
    engine.dispose()