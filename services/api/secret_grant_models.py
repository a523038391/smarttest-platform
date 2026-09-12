from datetime import datetime
from typing import Any

from sqlalchemy import (
    CHAR, JSON, CheckConstraint, DateTime, ForeignKey,
    ForeignKeyConstraint, Index, Integer, String, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base
from .quality_models import MYSQL_OPTIONS


class SecretCapabilityGrantModel(Base):
    __tablename__ = "secret_capability_grants"
    __table_args__ = (
        ForeignKeyConstraint(
            ["environment_id", "environment_revision"],
            ["environment_revisions.environment_id", "environment_revisions.revision"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("token_digest", name="uq_secret_grants_token_digest"),
        CheckConstraint("expires_at > issued_at", name="ck_secret_grants_expiry"),
        CheckConstraint(
            "consumed_at IS NULL OR consumed_at >= issued_at",
            name="ck_secret_grants_consumed_at",
        ),
        CheckConstraint(
            "revoked_at IS NULL OR revoked_at >= issued_at",
            name="ck_secret_grants_revoked_at",
        ),
        CheckConstraint(
            "consumed_at IS NULL OR revoked_at IS NULL",
            name="ck_secret_grants_single_terminal_state",
        ),
        Index("ix_secret_grants_attempt", "attempt_id"),
        Index("ix_secret_grants_expiry", "expires_at"),
        Index("ix_secret_grants_runner", "runner_id"),
        MYSQL_OPTIONS,
    )

    id: Mapped[str] = mapped_column(CHAR(36), primary_key=True)
    token_digest: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    task_id: Mapped[str] = mapped_column(CHAR(36), nullable=False)
    tenant_id: Mapped[str] = mapped_column(CHAR(36), nullable=False)
    project_id: Mapped[str] = mapped_column(CHAR(36), nullable=False)
    run_id: Mapped[str] = mapped_column(CHAR(36), nullable=False)
    attempt_id: Mapped[str] = mapped_column(
        CHAR(36), ForeignKey("run_attempts.id", ondelete="CASCADE"), nullable=False
    )
    runner_id: Mapped[str] = mapped_column(String(255), nullable=False)
    environment_id: Mapped[str] = mapped_column(CHAR(36), nullable=False)
    environment_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    reference_manifest: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


SecretGrantModel = SecretCapabilityGrantModel