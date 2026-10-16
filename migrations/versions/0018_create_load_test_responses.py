"""Create paginated load test response details.

Revision ID: 0018
Revises: 0017
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    options = {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"}
    op.create_table(
        "load_test_responses",
        sa.Column("id", sa.CHAR(36), nullable=False),
        sa.Column("run_id", sa.CHAR(36), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("target", sa.String(255), nullable=False),
        sa.Column("successful", sa.Boolean(), nullable=False),
        sa.Column("status", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Float(), nullable=False),
        sa.Column("error", sa.String(255), nullable=True),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("body_kind", sa.String(16), nullable=False),
        sa.Column("truncated", sa.Boolean(), nullable=False),
        sa.Column("body_bytes", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["load_test_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "sequence", name="uq_load_test_responses_run_sequence"),
        **options,
    )
    op.create_index(
        "ix_load_test_responses_run_sequence", "load_test_responses", ["run_id", "sequence"]
    )
    op.create_index(
        "ix_load_test_responses_run_success", "load_test_responses",
        ["run_id", "successful", "sequence"],
    )


def downgrade() -> None:
    op.drop_index("ix_load_test_responses_run_success", table_name="load_test_responses")
    op.drop_index("ix_load_test_responses_run_sequence", table_name="load_test_responses")
    op.drop_table("load_test_responses")