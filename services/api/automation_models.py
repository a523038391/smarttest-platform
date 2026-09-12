from datetime import datetime

from sqlalchemy import CHAR, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base
from .quality_models import MYSQL_OPTIONS


class AutomationScriptModel(Base):
    __tablename__ = "automation_scripts"
    __table_args__ = (
        Index("ix_automation_scripts_project_status", "project_id", "status"),
        MYSQL_OPTIONS,
    )

    id: Mapped[str] = mapped_column(CHAR(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(CHAR(36), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    engine: Mapped[str] = mapped_column(String(32), nullable=False)
    entrypoint: Mapped[str] = mapped_column(String(512), nullable=False)
    source_ref: Mapped[str] = mapped_column(String(512), nullable=False)
    content_digest: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    current_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    state_version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AutomationScriptRevisionModel(Base):
    __tablename__ = "automation_script_revisions"
    __table_args__ = (MYSQL_OPTIONS,)

    script_id: Mapped[str] = mapped_column(
        CHAR(36), ForeignKey("automation_scripts.id", ondelete="CASCADE"),
        primary_key=True,
    )
    revision: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    entrypoint: Mapped[str] = mapped_column(String(512), nullable=False)
    source_ref: Mapped[str] = mapped_column(String(512), nullable=False)
    content_digest: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class TestCaseScriptLinkModel(Base):
    __tablename__ = "test_case_script_links"
    __table_args__ = (
        Index("ix_test_case_script_links_script", "script_id"),
        MYSQL_OPTIONS,
    )

    test_case_id: Mapped[str] = mapped_column(
        CHAR(36), ForeignKey("test_cases.id", ondelete="CASCADE"), primary_key=True
    )
    script_id: Mapped[str] = mapped_column(
        CHAR(36), ForeignKey("automation_scripts.id", ondelete="CASCADE"),
        primary_key=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)