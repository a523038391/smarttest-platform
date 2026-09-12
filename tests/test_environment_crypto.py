from dataclasses import replace
from uuid import uuid4

import pytest

from services.api.environment_crypto import (
    SecretDecryptionError,
    SecretEncryptor,
    canonical_aad,
)
from services.api.environment_domain import ConfigurationCategory


def test_secret_encryption_is_randomized_and_aad_bound() -> None:
    encryptor = SecretEncryptor(b"k" * 32, "test-key")
    project_id, environment_id = uuid4(), uuid4()
    arguments = {
        "project_id": project_id,
        "environment_id": environment_id,
        "revision": 1,
        "category": ConfigurationCategory.ENVIRONMENT_VARIABLE,
        "name": "TOKEN",
    }
    first = encryptor.encrypt("sensitive", **arguments)
    second = encryptor.encrypt("sensitive", **arguments)

    assert (first.ciphertext, first.nonce, first.wrapped_dek) != (
        second.ciphertext, second.nonce, second.wrapped_dek
    )
    assert encryptor.decrypt(first, **arguments) == "sensitive"
    assert b"sensitive" not in first.ciphertext + first.wrapped_dek
    assert canonical_aad(**arguments) != canonical_aad(**(arguments | {"revision": 2}))


def test_secret_encryption_rejects_tampering_and_wrong_aad_generically() -> None:
    encryptor = SecretEncryptor(b"k" * 32, "test-key")
    arguments = {
        "project_id": uuid4(), "environment_id": uuid4(), "revision": 1,
        "category": ConfigurationCategory.COMMON_PARAMETER, "name": "password",
    }
    encrypted = encryptor.encrypt("sensitive", **arguments)
    tampered = replace(
        encrypted, ciphertext=bytes([encrypted.ciphertext[0] ^ 1]) + encrypted.ciphertext[1:]
    )

    with pytest.raises(SecretDecryptionError, match="secret value is unavailable"):
        encryptor.decrypt(tampered, **arguments)
    with pytest.raises(SecretDecryptionError, match="secret value is unavailable"):
        encryptor.decrypt(encrypted, **(arguments | {"name": "other"}))