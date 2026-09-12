from datetime import datetime
from typing import Any

from sqlalchemy import (
    CHAR,
    JSON,
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


class RunModel(Base):
    __tablename__ = "test_runs"
    __table_args__ = (
        UniqueConstraint(
            "idempotency_key", name="uq_test_runs_idempotency_key"
        ),
        Index("ix_test_runs_created_at", "created_at"),
        Index("ix_test_runs_state", "state"),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},
    )

    id: Mapped[str] = mapped_column(CHAR(36), primary_key=True)
    engine: Mapped[str] = mapped_column(String(32), nullable=False)
    parameters: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )
    fingerprint: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    state_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


Run = RunModel


class AttemptModel(Base):
    __tablename__ = "run_attempts"
    __table_args__ = (
        Index("ix_run_attempts_run_id", "run_id"),
        Index("ix_run_attempts_state", "state"),
        Index("ix_run_attempts_lease_expires_at", "lease_expires_at"),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},
    )

    id: Mapped[str] = mapped_column(CHAR(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        CHAR(36), ForeignKey("test_runs.id", ondelete="CASCADE"), nullable=False
    )
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    state_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    outcome: Mapped[str | None] = mapped_column(String(32), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    summary: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    runner_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class EventModel(Base):
    __tablename__ = "run_events"
    __table_args__ = (
        UniqueConstraint("event_id", name="uq_run_events_event_id"),
        UniqueConstraint("attempt_id", "seq", name="uq_run_events_attempt_seq"),
        Index("ix_run_events_run_cursor", "run_id", "cursor"),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},
    )

    cursor: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    event_id: Mapped[str] = mapped_column(CHAR(36), nullable=False)
    run_id: Mapped[str] = mapped_column(
        CHAR(36), ForeignKey("test_runs.id", ondelete="CASCADE"), nullable=False
    )
    attempt_id: Mapped[str] = mapped_column(
        CHAR(36), ForeignKey("run_attempts.id", ondelete="CASCADE"), nullable=False
    )
    engine: Mapped[str] = mapped_column(String(32), nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


class ArtifactModel(Base):
    __tablename__ = "run_artifacts"
    __table_args__ = (
        Index("ix_run_artifacts_run_id", "run_id"),
        Index("ix_run_artifacts_attempt_id", "attempt_id"),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},
    )

    id: Mapped[str] = mapped_column(CHAR(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        CHAR(36), ForeignKey("test_runs.id", ondelete="CASCADE"), nullable=False
    )
    attempt_id: Mapped[str] = mapped_column(
        CHAR(36), ForeignKey("run_attempts.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(255), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    storage_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


Attempt = AttemptModel
Event = EventModel
Artifact = ArtifactModel