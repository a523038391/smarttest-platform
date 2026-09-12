"""Create Data Factory workflows and persisted executions.

Revision ID: 0014
Revises: 0013
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    options = {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"}
    op.create_table(
        "data_factory_workflows",
        sa.Column("id", sa.CHAR(36), nullable=False),
        sa.Column("project_id", sa.CHAR(36), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("nodes", sa.JSON(), nullable=False),
        sa.Column("edges", sa.JSON(), nullable=False),
        sa.Column("variables", sa.JSON(), nullable=False),
        sa.Column("state_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "project_id", "name", name="uq_data_factory_workflows_project_name"
        ),
        **options,
    )
    op.create_index(
        "ix_data_factory_workflows_project", "data_factory_workflows",
        ["project_id"], unique=False,
    )
    op.create_table(
        "data_factory_runs",
        sa.Column("id", sa.CHAR(36), nullable=False),
        sa.Column("workflow_id", sa.CHAR(36), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("node_results", sa.JSON(), nullable=False),
        sa.Column("variables", sa.JSON(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["workflow_id"], ["data_factory_workflows.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        **options,
    )
    op.create_index(
        "ix_data_factory_runs_workflow_started", "data_factory_runs",
        ["workflow_id", "started_at"], unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_data_factory_runs_workflow_started", table_name="data_factory_runs"
    )
    op.drop_table("data_factory_runs")
    op.drop_index(
        "ix_data_factory_workflows_project", table_name="data_factory_workflows"
    )
    op.drop_table("data_factory_workflows")