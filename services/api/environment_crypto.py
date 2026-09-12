import json
import os
from uuid import UUID

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .environment_domain import ConfigurationCategory, EncryptedSecret


AAD_VERSION = 1
ALGORITHM = "AES-256-GCM"


class SecretEncryptionUnavailable(RuntimeError):
    pass


class SecretDecryptionError(RuntimeError):
    pass


def canonical_aad(
    project_id: UUID,
    environment_id: UUID,
    revision: int,
    category: ConfigurationCategory,
    name: str,
) -> bytes:
    return json.dumps(
        {
            "aad_version": AAD_VERSION,
            "category": category.value,
            "environment_id": str(environment_id),
            "name": name,
            "project_id": str(project_id),
            "revision": revision,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


class SecretEncryptor:
    def __init__(self, key: bytes | None, key_id: str | None) -> None:
        if (key is None) != (key_id is None):
            raise ValueError("secret key and key ID must be configured together")
        if key is not None and len(key) != 32:
            raise ValueError("secret key must contain exactly 32 bytes")
        if key_id is not None and (not key_id.strip() or len(key_id) > 255):
            raise ValueError("secret key ID must contain 1 to 255 characters")
        self._key = key
        self._key_id = key_id

    @property
    def available(self) -> bool:
        return self._key is not None and self._key_id is not None

    def encrypt(
        self,
        value: str,
        *,
        project_id: UUID,
        environment_id: UUID,
        revision: int,
        category: ConfigurationCategory,
        name: str,
    ) -> EncryptedSecret:
        key, key_id = self._require_key()
        aad = canonical_aad(project_id, environment_id, revision, category, name)
        dek = AESGCM.generate_key(bit_length=256)
        nonce, wrap_nonce = os.urandom(12), os.urandom(12)
        sealed = AESGCM(dek).encrypt(nonce, value.encode("utf-8"), aad)
        wrapped = AESGCM(key).encrypt(wrap_nonce, dek, aad + b"|dek-wrap")
        return EncryptedSecret(
            ciphertext=sealed[:-16],
            tag=sealed[-16:],
            nonce=nonce,
            wrapped_dek=wrapped,
            wrap_nonce=wrap_nonce,
            key_id=key_id,
            algorithm=ALGORITHM,
        )

    def decrypt(
        self,
        encrypted: EncryptedSecret,
        *,
        project_id: UUID,
        environment_id: UUID,
        revision: int,
        category: ConfigurationCategory,
        name: str,
    ) -> str:
        key, key_id = self._require_key()
        if encrypted.key_id != key_id or encrypted.algorithm != ALGORITHM:
            raise SecretDecryptionError("secret value is unavailable")
        aad = canonical_aad(project_id, environment_id, revision, category, name)
        try:
            dek = AESGCM(key).decrypt(
                encrypted.wrap_nonce, encrypted.wrapped_dek, aad + b"|dek-wrap"
            )
            plaintext = AESGCM(dek).decrypt(
                encrypted.nonce, encrypted.ciphertext + encrypted.tag, aad
            )
            return plaintext.decode("utf-8")
        except (InvalidTag, UnicodeDecodeError, ValueError):
            raise SecretDecryptionError("secret value is unavailable") from None

    def _require_key(self) -> tuple[bytes, str]:
        if not self.available:
            raise SecretEncryptionUnavailable("secret encryption is unavailable")
        return self._key, self._key_id  # type: ignore[return-value]