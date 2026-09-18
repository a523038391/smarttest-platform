from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, JSON, CHAR, DateTime, Float, ForeignKeyConstraint, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base
from .quality_models import MYSQL_OPTIONS


class LoadTestModel(Base):
    __tablename__ = "load_tests"
    __table_args__ = (
        ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(
            ["environment_id"], ["project_environments.id"], ondelete="RESTRICT"
        ),
        UniqueConstraint("project_id", "name", name="uq_load_tests_project_name"),
        Index("ix_load_tests_project", "project_id"),
        Index("ix_load_tests_environment", "environment_id"),
        MYSQL_OPTIONS,
    )

    id: Mapped[str] = mapped_column(CHAR(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(CHAR(36), nullable=False)
    environment_id: Mapped[str | None] = mapped_column(CHAR(36), nullable=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    targets: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    request_count: Mapped[int] = mapped_column(Integer, nullable=False)
    duration_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    concurrency: Mapped[int] = mapped_column(Integer, nullable=False)
    interval_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    timeout_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    traffic_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="REQUESTS")
    initial_variables: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    stop_on_failure: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    state_version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class LoadTestRunModel(Base):
    __tablename__ = "load_test_runs"
    __table_args__ = (
        ForeignKeyConstraint(["load_test_id"], ["load_tests.id"], ondelete="CASCADE"),
        Index("ix_load_test_runs_test_started", "load_test_id", "started_at"),
        Index("ix_load_test_runs_test_status", "load_test_id", "status"),
        MYSQL_OPTIONS,
    )

    id: Mapped[str] = mapped_column(CHAR(36), primary_key=True)
    load_test_id: Mapped[str] = mapped_column(CHAR(36), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    error_message: Mapped[str | None] = mapped_column(String(255), nullable=True)