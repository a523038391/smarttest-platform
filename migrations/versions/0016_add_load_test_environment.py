"""Add an optional project environment to load tests.

Revision ID: 0016
Revises: 0015
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("load_tests") as batch:
        batch.add_column(sa.Column("environment_id", sa.CHAR(36), nullable=True))
        batch.create_foreign_key(
            "fk_load_tests_environment", "project_environments",
            ["environment_id"], ["id"], ondelete="RESTRICT",
        )
    op.create_index(
        "ix_load_tests_environment", "load_tests", ["environment_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_load_tests_environment", table_name="load_tests")
    with op.batch_alter_table("load_tests") as batch:
        batch.drop_constraint("fk_load_tests_environment", type_="foreignkey")
        batch.drop_column("environment_id")