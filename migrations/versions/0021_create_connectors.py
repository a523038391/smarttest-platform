"""Create global connector definitions.

Revision ID: 0021
Revises: 0020
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    options = {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"}
    op.create_table(
        "connectors",
        sa.Column("id", sa.CHAR(36), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("base_url", sa.String(8192), nullable=False),
        sa.Column("auth_type", sa.String(16), nullable=False),
        sa.Column("auth_header", sa.String(255), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("timeout_seconds", sa.Integer(), nullable=False),
        sa.Column("state_version", sa.Integer(), nullable=False),
        sa.Column("secret_revision", sa.Integer(), nullable=False),
        sa.Column("ciphertext", sa.LargeBinary(), nullable=True),
        sa.Column("tag", sa.LargeBinary(16), nullable=True),
        sa.Column("nonce", sa.LargeBinary(12), nullable=True),
        sa.Column("wrapped_dek", sa.LargeBinary(), nullable=True),
        sa.Column("wrap_nonce", sa.LargeBinary(12), nullable=True),
        sa.Column("key_id", sa.String(255), nullable=True),
        sa.Column("algorithm", sa.String(32), nullable=True),
        sa.Column("tools", sa.JSON(), nullable=False),
        sa.Column("last_tested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_test_success", sa.Boolean(), nullable=True),
        sa.Column("last_error", sa.String(512), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("timeout_seconds >= 1 AND timeout_seconds <= 60",
                           name="ck_connectors_timeout"),
        sa.CheckConstraint(
            "(auth_type = 'NONE' AND ciphertext IS NULL AND tag IS NULL "
            "AND nonce IS NULL AND wrapped_dek IS NULL AND wrap_nonce IS NULL "
            "AND key_id IS NULL AND algorithm IS NULL) OR "
            "(auth_type <> 'NONE' AND ciphertext IS NOT NULL AND tag IS NOT NULL "
            "AND nonce IS NOT NULL AND wrapped_dek IS NOT NULL AND wrap_nonce IS NOT NULL "
            "AND key_id IS NOT NULL AND algorithm IS NOT NULL)",
            name="ck_connectors_credential_material",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_connectors_name"),
        **options,
    )


def downgrade() -> None:
    op.drop_table("connectors")