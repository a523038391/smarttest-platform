from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON, Boolean, CHAR, CheckConstraint, DateTime, ForeignKeyConstraint, Index,
    Integer, LargeBinary, String, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base
from .quality_models import MYSQL_OPTIONS


class EnvironmentModel(Base):
    __tablename__ = "project_environments"
    __table_args__ = (
        UniqueConstraint("project_id", "name", name="uq_environments_project_name"),
        Index("ix_environments_project_status", "project_id", "status"),
        MYSQL_OPTIONS,
    )

    id: Mapped[str] = mapped_column(CHAR(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(CHAR(36), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    current_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    state_version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class EnvironmentRevisionModel(Base):
    __tablename__ = "environment_revisions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["environment_id"], ["project_environments.id"], ondelete="CASCADE"
        ),
        MYSQL_OPTIONS,
    )

    environment_id: Mapped[str] = mapped_column(CHAR(36), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class EnvironmentValueModel(Base):
    __tablename__ = "environment_revision_values"
    __table_args__ = (
        ForeignKeyConstraint(
            ["environment_id", "revision"],
            ["environment_revisions.environment_id", "environment_revisions.revision"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("secret_ref", name="uq_environment_values_secret_ref"),
        CheckConstraint(
            "(is_secret = 1 AND public_value IS NULL AND secret_ref IS NOT NULL "
            "AND ciphertext IS NOT NULL AND tag IS NOT NULL AND nonce IS NOT NULL "
            "AND wrapped_dek IS NOT NULL AND wrap_nonce IS NOT NULL "
            "AND key_id IS NOT NULL AND algorithm IS NOT NULL) OR "
            "(is_secret = 0 AND secret_ref IS NULL AND ciphertext IS NULL "
            "AND tag IS NULL AND nonce IS NULL AND wrapped_dek IS NULL "
            "AND wrap_nonce IS NULL AND key_id IS NULL AND algorithm IS NULL)",
            name="ck_environment_values_secret_material",
        ),
        MYSQL_OPTIONS,
    )

    environment_id: Mapped[str] = mapped_column(CHAR(36), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, primary_key=True)
    category: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), primary_key=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    is_secret: Mapped[bool] = mapped_column(Boolean, nullable=False)
    public_value: Mapped[Any | None] = mapped_column(JSON(none_as_null=True), nullable=True)
    secret_ref: Mapped[str | None] = mapped_column(CHAR(36), nullable=True)
    ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    tag: Mapped[bytes | None] = mapped_column(LargeBinary(16), nullable=True)
    nonce: Mapped[bytes | None] = mapped_column(LargeBinary(12), nullable=True)
    wrapped_dek: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    wrap_nonce: Mapped[bytes | None] = mapped_column(LargeBinary(12), nullable=True)
    key_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    algorithm: Mapped[str | None] = mapped_column(String(32), nullable=True)