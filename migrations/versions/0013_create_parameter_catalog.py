"""Create parameter enum catalog and script parameter definitions.

Revision ID: 0013
Revises: 0012
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    options = {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"}
    op.create_table(
        "parameter_enum_sets",
        sa.Column("id", sa.CHAR(36), nullable=False),
        sa.Column("project_id", sa.CHAR(36), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("options", sa.JSON(), nullable=False),
        sa.Column("state_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "project_id", "name", name="uq_parameter_enums_project_name"
        ),
        **options,
    )
    op.create_table(
        "automation_script_parameters",
        sa.Column("script_id", sa.CHAR(36), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("label", sa.String(255), nullable=False),
        sa.Column("input_type", sa.String(32), nullable=False),
        sa.Column("required", sa.Boolean(), nullable=False),
        sa.Column("enum_set_id", sa.CHAR(36), nullable=True),
        sa.Column("default_value", sa.JSON(none_as_null=True), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["script_id"], ["automation_scripts.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["enum_set_id"], ["parameter_enum_sets.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("script_id", "name"),
        sa.UniqueConstraint(
            "script_id", "position", name="uq_script_parameters_position"
        ),
        **options,
    )
    op.create_index(
        "ix_script_parameters_enum_set", "automation_script_parameters",
        ["enum_set_id"], unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_script_parameters_enum_set", table_name="automation_script_parameters"
    )
    op.drop_table("automation_script_parameters")
    op.drop_table("parameter_enum_sets")