import hmac
from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import delete, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from .auth_domain import (
    PasswordDigest, UserRecord, UserRole, normalize_username, validate_username,
)
from .auth_models import AuthBootstrapModel, UserModel, UserSessionModel
from .auth_repository import AuthAlreadyInitialized, UsernameConflict


class SqlAuthRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def is_initialized(self) -> bool:
        with self._session_factory() as session:
            return session.get(AuthBootstrapModel, 1) is not None

    def initialize_admin(
        self, username: str, display_name: str, password: PasswordDigest
    ) -> UserRecord:
        normalized = validate_username(username)
        now = datetime.now(timezone.utc)
        user_id = uuid4()
        try:
            with self._session_factory() as session, session.begin():
                if session.get(AuthBootstrapModel, 1) is not None:
                    raise AuthAlreadyInitialized("authentication is already initialized")
                session.add(UserModel(
                    id=str(user_id), username=normalized, display_name=display_name,
                    role=UserRole.ADMIN.value, is_active=True,
                    password_salt=password.salt, password_hash=password.digest,
                    password_iterations=password.iterations, created_at=now, updated_at=now,
                ))
                session.flush()
                session.add(AuthBootstrapModel(
                    singleton_key=1, user_id=str(user_id), initialized_at=now
                ))
        except IntegrityError:
            if self.is_initialized():
                raise AuthAlreadyInitialized(
                    "authentication is already initialized"
                ) from None
            raise UsernameConflict("username is already in use") from None
        return UserRecord(
            user_id, normalized, display_name, UserRole.ADMIN, True,
            now, now, password,
        )

    def find_user(self, username: str) -> UserRecord | None:
        with self._session_factory() as session:
            model = session.scalar(select(UserModel).where(
                UserModel.username == normalize_username(username)
            ))
            return self._record(model) if model is not None else None

    def create_session(
        self, user_id: UUID, token_digest: bytes, expires_at: datetime
    ) -> None:
        now = datetime.now(timezone.utc)
        with self._session_factory() as session, session.begin():
            session.execute(delete(UserSessionModel).where(or_(
                UserSessionModel.expires_at <= now,
                UserSessionModel.revoked_at.is_not(None),
            )))
            session.add(UserSessionModel(
                id=str(uuid4()), user_id=str(user_id), token_digest=token_digest,
                expires_at=expires_at, created_at=now, revoked_at=None,
            ))

    def resolve_session(
        self, token_digest: bytes, now: datetime | None = None
    ) -> UserRecord | None:
        current = now or datetime.now(timezone.utc)
        with self._session_factory() as session:
            session_model = session.scalar(select(UserSessionModel).where(
                UserSessionModel.token_digest == token_digest,
                UserSessionModel.revoked_at.is_(None),
                UserSessionModel.expires_at > current,
            ))
            if (
                session_model is None
                or not hmac.compare_digest(session_model.token_digest, token_digest)
            ):
                return None
            user = session.get(UserModel, session_model.user_id)
            if user is None or not user.is_active:
                return None
            return self._record(user)

    def revoke_session(self, token_digest: bytes, now: datetime | None = None) -> None:
        with self._session_factory() as session, session.begin():
            model = session.scalar(select(UserSessionModel).where(
                UserSessionModel.token_digest == token_digest
            ).with_for_update())
            if model is not None and hmac.compare_digest(model.token_digest, token_digest):
                model.revoked_at = model.revoked_at or now or datetime.now(timezone.utc)

    @classmethod
    def _record(cls, model: UserModel) -> UserRecord:
        return UserRecord(
            UUID(model.id), model.username, model.display_name, UserRole(model.role),
            model.is_active, cls._utc(model.created_at), cls._utc(model.updated_at),
            PasswordDigest(model.password_salt, model.password_hash, model.password_iterations),
        )

    @staticmethod
    def _utc(value: datetime) -> datetime:
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value