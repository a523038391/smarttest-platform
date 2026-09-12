from datetime import datetime
from typing import Any

from sqlalchemy import (
    CHAR, JSON, CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Index, Integer,
    String, Text, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base
from .quality_models import MYSQL_OPTIONS


class TestPlanModel(Base):
    __tablename__ = "test_plans"
    __table_args__ = (
        CheckConstraint("max_parallel BETWEEN 1 AND 100", name="ck_test_plans_max_parallel"),
        Index("ix_test_plans_project_status", "project_id", "status"),
        MYSQL_OPTIONS,
    )

    id: Mapped[str] = mapped_column(CHAR(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(CHAR(36), nullable=False)
    project_id: Mapped[str] = mapped_column(CHAR(36), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    definition: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    max_parallel: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    current_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    state_version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class TestPlanRevisionModel(Base):
    __tablename__ = "test_plan_revisions"
    __table_args__ = (
        CheckConstraint(
            "max_parallel BETWEEN 1 AND 100",
            name="ck_test_plan_revisions_max_parallel",
        ),
        MYSQL_OPTIONS,
    )

    plan_id: Mapped[str] = mapped_column(
        CHAR(36), ForeignKey("test_plans.id", ondelete="CASCADE"), primary_key=True
    )
    revision: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    definition: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    max_parallel: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ExecutionBatchModel(Base):
    __tablename__ = "test_plan_execution_batches"
    __table_args__ = (
        ForeignKeyConstraint(
            ["plan_id", "plan_revision"],
            ["test_plan_revisions.plan_id", "test_plan_revisions.revision"],
        ),
        UniqueConstraint(
            "tenant_id", "project_id", "idempotency_key",
            name="uq_plan_batches_scope_key",
        ),
        CheckConstraint(
            "max_parallel BETWEEN 1 AND 100", name="ck_plan_batches_max_parallel"
        ),
        Index("ix_plan_batches_plan", "plan_id", "plan_revision"),
        MYSQL_OPTIONS,
    )

    id: Mapped[str] = mapped_column(CHAR(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(CHAR(36), nullable=False)
    project_id: Mapped[str] = mapped_column(CHAR(36), nullable=False)
    plan_id: Mapped[str] = mapped_column(CHAR(36), nullable=False)
    plan_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    max_parallel: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RunSpecModel(Base):
    __tablename__ = "run_specs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["plan_id", "plan_revision"],
            ["test_plan_revisions.plan_id", "test_plan_revisions.revision"],
        ),
        ForeignKeyConstraint(
            ["script_id", "script_revision"],
            ["automation_script_revisions.script_id", "automation_script_revisions.revision"],
        ),
        ForeignKeyConstraint(
            ["environment_id", "environment_revision"],
            ["environment_revisions.environment_id", "environment_revisions.revision"],
        ),
        UniqueConstraint("run_id", name="uq_run_specs_run_id"),
        UniqueConstraint("batch_id", "position", name="uq_run_specs_batch_position"),
        CheckConstraint(
            "(environment_id IS NULL AND environment_revision IS NULL) OR "
            "(environment_id IS NOT NULL AND environment_revision IS NOT NULL)",
            name="ck_run_specs_environment_pin",
        ),
        Index("ix_run_specs_batch", "batch_id"),
        MYSQL_OPTIONS,
    )

    id: Mapped[str] = mapped_column(CHAR(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        CHAR(36), ForeignKey("test_runs.id", ondelete="CASCADE"), nullable=False
    )
    batch_id: Mapped[str] = mapped_column(
        CHAR(36), ForeignKey("test_plan_execution_batches.id", ondelete="CASCADE"),
        nullable=False,
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    tenant_id: Mapped[str] = mapped_column(CHAR(36), nullable=False)
    project_id: Mapped[str] = mapped_column(CHAR(36), nullable=False)
    plan_id: Mapped[str] = mapped_column(CHAR(36), nullable=False)
    plan_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    item_id: Mapped[str] = mapped_column(CHAR(36), nullable=False)
    row_key: Mapped[str] = mapped_column(String(255), nullable=False)
    engine: Mapped[str] = mapped_column(String(32), nullable=False)
    script_id: Mapped[str] = mapped_column(CHAR(36), nullable=False)
    script_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    source_ref: Mapped[str] = mapped_column(String(512), nullable=False)
    content_digest: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    entrypoint: Mapped[str] = mapped_column(String(512), nullable=False)
    timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    environment_id: Mapped[str | None] = mapped_column(CHAR(36), nullable=True)
    environment_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    environment_variables: Mapped[dict[str, str]] = mapped_column(JSON, nullable=False)
    parameters: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    secret_references: Mapped[list[dict[str, str]]] = mapped_column(JSON, nullable=False)
    execution_policy: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)