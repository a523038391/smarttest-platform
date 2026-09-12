import hashlib
import hmac
import re
import secrets
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from uuid import UUID


PASSWORD_ITERATIONS = 600_000
PASSWORD_SALT_BYTES = 32
SESSION_TOKEN_BYTES = 32
USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9._-]{3,64}$")


class UserRole(StrEnum):
    ADMIN = "ADMIN"


@dataclass(frozen=True, slots=True)
class PasswordDigest:
    salt: bytes = field(repr=False)
    digest: bytes = field(repr=False)
    iterations: int = PASSWORD_ITERATIONS

    def __post_init__(self) -> None:
        if self.iterations < PASSWORD_ITERATIONS:
            raise ValueError(f"password iterations must be at least {PASSWORD_ITERATIONS}")
        if len(self.salt) < 16 or len(self.digest) != 32:
            raise ValueError("password digest has invalid dimensions")


@dataclass(frozen=True, slots=True)
class UserRecord:
    id: UUID
    username: str
    display_name: str
    role: UserRole
    is_active: bool
    created_at: datetime
    updated_at: datetime
    password: PasswordDigest = field(repr=False, compare=False)


def normalize_username(username: str) -> str:
    return unicodedata.normalize("NFKC", username.strip()).casefold()


def validate_username(username: str) -> str:
    normalized = normalize_username(username)
    if not USERNAME_PATTERN.fullmatch(normalized):
        raise ValueError("username contains unsafe characters")
    return normalized


def hash_password(password: str, *, salt: bytes | None = None) -> PasswordDigest:
    resolved_salt = salt or secrets.token_bytes(PASSWORD_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), resolved_salt, PASSWORD_ITERATIONS
    )
    return PasswordDigest(resolved_salt, digest)


def verify_password(password: str, expected: PasswordDigest) -> bool:
    actual = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), expected.salt, expected.iterations
    )
    return hmac.compare_digest(actual, expected.digest)


def new_session_token() -> str:
    return secrets.token_urlsafe(SESSION_TOKEN_BYTES)


def session_token_digest(token: str) -> bytes:
    return hashlib.sha256(token.encode("utf-8")).digest()