"""Create project-scoped load test definitions and runs.

Revision ID: 0015
Revises: 0014
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    options = {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"}
    op.create_table(
        "load_tests",
        sa.Column("id", sa.CHAR(36), nullable=False),
        sa.Column("project_id", sa.CHAR(36), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("targets", sa.JSON(), nullable=False),
        sa.Column("mode", sa.String(16), nullable=False),
        sa.Column("request_count", sa.Integer(), nullable=False),
        sa.Column("duration_seconds", sa.Integer(), nullable=False),
        sa.Column("concurrency", sa.Integer(), nullable=False),
        sa.Column("interval_ms", sa.Integer(), nullable=False),
        sa.Column("timeout_seconds", sa.Float(), nullable=False),
        sa.Column("state_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "name", name="uq_load_tests_project_name"),
        **options,
    )
    op.create_index("ix_load_tests_project", "load_tests", ["project_id"])
    op.create_table(
        "load_test_runs",
        sa.Column("id", sa.CHAR(36), nullable=False),
        sa.Column("load_test_id", sa.CHAR(36), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("mode", sa.String(16), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metrics", sa.JSON(), nullable=False),
        sa.Column("error_message", sa.String(255), nullable=True),
        sa.ForeignKeyConstraint(["load_test_id"], ["load_tests.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        **options,
    )
    op.create_index(
        "ix_load_test_runs_test_started", "load_test_runs", ["load_test_id", "started_at"]
    )
    op.create_index(
        "ix_load_test_runs_test_status", "load_test_runs", ["load_test_id", "status"]
    )


def downgrade() -> None:
    op.drop_index("ix_load_test_runs_test_status", table_name="load_test_runs")
    op.drop_index("ix_load_test_runs_test_started", table_name="load_test_runs")
    op.drop_table("load_test_runs")
    op.drop_index("ix_load_tests_project", table_name="load_tests")
    op.drop_table("load_tests")