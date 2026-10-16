"""Expand MySQL response bodies to MEDIUMTEXT.

Revision ID: 0019
Revises: 0018
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if op.get_bind().dialect.name == "mysql":
        op.alter_column(
            "load_test_responses", "body", existing_type=sa.Text(),
            type_=mysql.MEDIUMTEXT(), existing_nullable=True,
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "mysql":
        op.alter_column(
            "load_test_responses", "body", existing_type=mysql.MEDIUMTEXT(),
            type_=sa.Text(), existing_nullable=True,
        )