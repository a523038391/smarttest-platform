"""Add load test categories.

Revision ID: 0020
Revises: 0019
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "load_tests",
        sa.Column("category", sa.String(length=100), nullable=False, server_default=""),
    )
    op.create_index("ix_load_tests_project_category", "load_tests", ["project_id", "category"])


def downgrade() -> None:
    op.drop_index("ix_load_tests_project_category", table_name="load_tests")
    op.drop_column("load_tests", "category")