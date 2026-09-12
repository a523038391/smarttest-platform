import hmac
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import RLock
from typing import Protocol
from uuid import UUID, uuid4

from .auth_domain import (
    PasswordDigest, UserRecord, UserRole, normalize_username, validate_username,
)


class AuthAlreadyInitialized(ValueError):
    pass


class UsernameConflict(ValueError):
    pass


class InvalidCredentials(LookupError):
    pass


class AuthRepositoryLike(Protocol):
    def is_initialized(self) -> bool: ...
    def initialize_admin(
        self, username: str, display_name: str, password: PasswordDigest
    ) -> UserRecord: ...
    def find_user(self, username: str) -> UserRecord | None: ...
    def create_session(
        self, user_id: UUID, token_digest: bytes, expires_at: datetime
    ) -> None: ...
    def resolve_session(
        self, token_digest: bytes, now: datetime | None = None
    ) -> UserRecord | None: ...
    def revoke_session(self, token_digest: bytes, now: datetime | None = None) -> None: ...


@dataclass(slots=True)
class _Session:
    user_id: UUID
    token_digest: bytes
    expires_at: datetime
    revoked_at: datetime | None = None


class AuthRepository:
    def __init__(self) -> None:
        self._lock = RLock()
        self._initialized = False
        self._users: dict[UUID, UserRecord] = {}
        self._usernames: dict[str, UUID] = {}
        self._sessions: list[_Session] = []

    def is_initialized(self) -> bool:
        with self._lock:
            return self._initialized

    def initialize_admin(
        self, username: str, display_name: str, password: PasswordDigest
    ) -> UserRecord:
        normalized = validate_username(username)
        with self._lock:
            if self._initialized:
                raise AuthAlreadyInitialized("authentication is already initialized")
            if normalized in self._usernames:
                raise UsernameConflict("username is already in use")
            now = datetime.now(timezone.utc)
            user = UserRecord(
                uuid4(), normalized, display_name, UserRole.ADMIN, True,
                now, now, password,
            )
            self._users[user.id] = user
            self._usernames[normalized] = user.id
            self._initialized = True
            return user

    def find_user(self, username: str) -> UserRecord | None:
        with self._lock:
            user_id = self._usernames.get(normalize_username(username))
            return self._users.get(user_id) if user_id is not None else None

    def create_session(
        self, user_id: UUID, token_digest: bytes, expires_at: datetime
    ) -> None:
        with self._lock:
            now = datetime.now(timezone.utc)
            self._sessions = [
                item for item in self._sessions
                if item.revoked_at is None and item.expires_at > now
            ]
            self._sessions.append(_Session(user_id, token_digest, expires_at))

    def resolve_session(
        self, token_digest: bytes, now: datetime | None = None
    ) -> UserRecord | None:
        current = now or datetime.now(timezone.utc)
        with self._lock:
            for session in self._sessions:
                if hmac.compare_digest(session.token_digest, token_digest):
                    user = self._users.get(session.user_id)
                    if (
                        session.revoked_at is None
                        and session.expires_at > current
                        and user is not None
                        and user.is_active
                    ):
                        return user
                    return None
        return None

    def revoke_session(self, token_digest: bytes, now: datetime | None = None) -> None:
        current = now or datetime.now(timezone.utc)
        with self._lock:
            for session in self._sessions:
                if hmac.compare_digest(session.token_digest, token_digest):
                    session.revoked_at = session.revoked_at or current
                    return


MemoryAuthRepository = AuthRepository