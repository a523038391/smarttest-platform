from datetime import datetime

from sqlalchemy import (
    BINARY, Boolean, CHAR, CheckConstraint, DateTime, ForeignKey, Index, Integer,
    String, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base
from .quality_models import MYSQL_OPTIONS


class UserModel(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            "password_iterations >= 600000", name="ck_users_password_iterations"
        ),
        CheckConstraint("role = 'ADMIN'", name="ck_users_role"),
        UniqueConstraint("username", name="uq_users_username"),
        MYSQL_OPTIONS,
    )

    id: Mapped[str] = mapped_column(CHAR(36), primary_key=True)
    username: Mapped[str] = mapped_column(String(64), nullable=False)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False)
    password_salt: Mapped[bytes] = mapped_column(BINARY(32), nullable=False)
    password_hash: Mapped[bytes] = mapped_column(BINARY(32), nullable=False)
    password_iterations: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class UserSessionModel(Base):
    __tablename__ = "user_sessions"
    __table_args__ = (
        UniqueConstraint("token_digest", name="uq_user_sessions_token_digest"),
        Index("ix_user_sessions_user", "user_id"),
        Index("ix_user_sessions_expires", "expires_at"),
        MYSQL_OPTIONS,
    )

    id: Mapped[str] = mapped_column(CHAR(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        CHAR(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    token_digest: Mapped[bytes] = mapped_column(BINARY(32), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuthBootstrapModel(Base):
    __tablename__ = "auth_bootstrap"
    __table_args__ = (
        CheckConstraint("singleton_key = 1", name="ck_auth_bootstrap_singleton"),
        UniqueConstraint("user_id", name="uq_auth_bootstrap_user"),
        MYSQL_OPTIONS,
    )

    singleton_key: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=False
    )
    user_id: Mapped[str] = mapped_column(
        CHAR(36), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False,
    )
    initialized_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)