"""Add Runner ownership and heartbeat leases.

Revision ID: 0003
Revises: 0002
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("run_attempts", sa.Column("runner_id", sa.String(255), nullable=True))
    op.add_column(
        "run_attempts", sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "run_attempts",
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_run_attempts_lease_expires_at", "run_attempts", ["lease_expires_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_run_attempts_lease_expires_at", table_name="run_attempts")
    op.drop_column("run_attempts", "lease_expires_at")
    op.drop_column("run_attempts", "heartbeat_at")
    op.drop_column("run_attempts", "runner_id")