"""Create the test_runs table.

Revision ID: 0001
Revises:
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "test_runs",
        sa.Column("id", sa.CHAR(length=36), nullable=False),
        sa.Column("engine", sa.String(length=32), nullable=False),
        sa.Column("parameters", sa.JSON(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=True),
        sa.Column("fingerprint", sa.CHAR(length=64), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "state_version", sa.Integer(), server_default="0", nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "idempotency_key", name="uq_test_runs_idempotency_key"
        ),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
    )
    op.create_index("ix_test_runs_created_at", "test_runs", ["created_at"])
    op.create_index("ix_test_runs_state", "test_runs", ["state"])


def downgrade() -> None:
    op.drop_index("ix_test_runs_state", table_name="test_runs")
    op.drop_index("ix_test_runs_created_at", table_name="test_runs")
    op.drop_table("test_runs")