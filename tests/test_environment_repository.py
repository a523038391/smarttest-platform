from uuid import uuid4

import pytest

from packages.protocol import SecretReference
from services.api.environment_crypto import SecretEncryptor
from services.api.environment_domain import (
    ConfigurationCategory,
    ConfigurationValueInput,
    EnvironmentStatus,
    InvalidEnvironmentTransition,
)
from services.api.environment_repository import (
    ConfigurationResolver,
    EnvironmentRepository,
    EnvironmentVersionConflict,
)
from services.api.environment_schemas import EnvironmentResponse


def test_environment_revisions_redaction_and_pinned_resolution() -> None:
    encryptor = SecretEncryptor(b"e" * 32, "key-1")
    repository = EnvironmentRepository(encryptor)
    project_id = uuid4()
    created = repository.create_environment(
        project_id, "staging",
        [ConfigurationValueInput("BASE_URL", "https://example.test")],
        [
            ConfigurationValueInput("retries", 3),
            ConfigurationValueInput("password", "secret", secret=True),
        ],
    )
    secret = next(item for item in created.values if item.secret)
    active = repository.update_environment(
        created.id, expected_version=0, name=created.name,
        status=EnvironmentStatus.ACTIVE,
        environment_variables=[ConfigurationValueInput("BASE_URL", "https://example.test")],
        common_parameters=[
            ConfigurationValueInput("retries", 3),
            ConfigurationValueInput(
                "password", secret=True, secret_ref=secret.secret_ref
            ),
        ],
    )
    active_secret = next(item for item in active.values if item.secret)
    response = EnvironmentResponse.from_record(active).model_dump(
        mode="json", exclude_none=True
    )
    snapshot = ConfigurationResolver(repository, encryptor).snapshot(
        project_id, active.id, active.revision,
        [SecretReference(
            category=ConfigurationCategory.COMMON_PARAMETER.value,
            name="password", secret_ref=active_secret.secret_ref,
        )],
    )

    assert [item.revision for item in repository.list_revisions(active.id)] == [1, 2]
    assert response["common_parameters"][1] == {
        "name": "password", "secret": True, "configured": True,
        "secret_ref": str(active_secret.secret_ref),
    }
    assert "ciphertext" not in str(response)
    assert snapshot.common_parameters == {"password": "secret", "retries": 3}
    assert "secret" not in repr(snapshot)


def test_environment_optimistic_version_and_terminal_archive_guard() -> None:
    repository = EnvironmentRepository()
    environment = repository.create_environment(uuid4(), "public", [], [])
    with pytest.raises(EnvironmentVersionConflict):
        repository.update_environment(
            environment.id, expected_version=1, name="public",
            status=EnvironmentStatus.DRAFT,
            environment_variables=[], common_parameters=[],
        )
    active = repository.update_environment(
        environment.id, expected_version=0, name="public",
        status=EnvironmentStatus.ACTIVE,
        environment_variables=[], common_parameters=[],
    )
    archived = repository.update_environment(
        environment.id, expected_version=1, name="public",
        status=EnvironmentStatus.ARCHIVED,
        environment_variables=[], common_parameters=[],
    )
    with pytest.raises(InvalidEnvironmentTransition):
        repository.update_environment(
            environment.id, expected_version=archived.state_version, name="public",
            status=EnvironmentStatus.ARCHIVED,
            environment_variables=[], common_parameters=[],
        )
    assert active.revision == 2