from datetime import datetime
from typing import Any

from sqlalchemy import JSON, CHAR, DateTime, ForeignKeyConstraint, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base
from .quality_models import MYSQL_OPTIONS


class DataFactoryWorkflowModel(Base):
    __tablename__ = "data_factory_workflows"
    __table_args__ = (
        ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="RESTRICT"),
        UniqueConstraint("project_id", "name", name="uq_data_factory_workflows_project_name"),
        Index("ix_data_factory_workflows_project", "project_id"),
        MYSQL_OPTIONS,
    )

    id: Mapped[str] = mapped_column(CHAR(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(CHAR(36), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    nodes: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    edges: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    variables: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    state_version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class DataFactoryRunModel(Base):
    __tablename__ = "data_factory_runs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workflow_id"], ["data_factory_workflows.id"], ondelete="CASCADE"
        ),
        Index("ix_data_factory_runs_workflow_started", "workflow_id", "started_at"),
        MYSQL_OPTIONS,
    )

    id: Mapped[str] = mapped_column(CHAR(36), primary_key=True)
    workflow_id: Mapped[str] = mapped_column(CHAR(36), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    node_results: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    variables: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)