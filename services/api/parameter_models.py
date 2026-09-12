from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON, Boolean, CHAR, DateTime, ForeignKey, Index, Integer, String, Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base
from .quality_models import MYSQL_OPTIONS


class ParameterEnumSetModel(Base):
    __tablename__ = "parameter_enum_sets"
    __table_args__ = (
        UniqueConstraint("project_id", "name", name="uq_parameter_enums_project_name"),
        MYSQL_OPTIONS,
    )

    id: Mapped[str] = mapped_column(CHAR(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(CHAR(36), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    options: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    state_version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AutomationScriptParameterModel(Base):
    __tablename__ = "automation_script_parameters"
    __table_args__ = (
        UniqueConstraint("script_id", "position", name="uq_script_parameters_position"),
        Index("ix_script_parameters_enum_set", "enum_set_id"),
        MYSQL_OPTIONS,
    )

    script_id: Mapped[str] = mapped_column(
        CHAR(36), ForeignKey("automation_scripts.id", ondelete="CASCADE"), primary_key=True
    )
    name: Mapped[str] = mapped_column(String(128), primary_key=True)
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    input_type: Mapped[str] = mapped_column(String(32), nullable=False)
    required: Mapped[bool] = mapped_column(Boolean, nullable=False)
    enum_set_id: Mapped[str | None] = mapped_column(
        CHAR(36), ForeignKey("parameter_enum_sets.id", ondelete="RESTRICT"), nullable=True
    )
    default_value: Mapped[Any | None] = mapped_column(
        JSON(none_as_null=True), nullable=True
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)