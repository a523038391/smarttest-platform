"""Add scenario traffic settings to load tests.

Revision ID: 0017
Revises: 0016
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("load_tests") as batch:
        batch.add_column(sa.Column(
            "traffic_mode", sa.String(16), nullable=False,
            server_default="REQUESTS",
        ))
        batch.add_column(sa.Column("initial_variables", sa.JSON(), nullable=True))
        batch.add_column(sa.Column(
            "stop_on_failure", sa.Boolean(), nullable=False,
            server_default=sa.true(),
        ))
    op.execute(sa.text(
        "UPDATE load_tests SET initial_variables = '{}' WHERE initial_variables IS NULL"
    ))
    with op.batch_alter_table("load_tests") as batch:
        batch.alter_column(
            "initial_variables", existing_type=sa.JSON(), nullable=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("load_tests") as batch:
        batch.drop_column("stop_on_failure")
        batch.drop_column("initial_variables")
        batch.drop_column("traffic_mode")