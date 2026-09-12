"""Create versioned project execution environments.

Revision ID: 0007
Revises: 0006
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    options = {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"}
    op.create_table(
        "project_environments",
        sa.Column("id", sa.CHAR(36), primary_key=True),
        sa.Column("project_id", sa.CHAR(36), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("current_revision", sa.Integer(), nullable=False),
        sa.Column("state_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "project_id", "name", name="uq_environments_project_name"
        ),
        **options,
    )
    op.create_index(
        "ix_environments_project_status", "project_environments",
        ["project_id", "status"],
    )
    op.create_table(
        "environment_revisions",
        sa.Column("environment_id", sa.CHAR(36), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["environment_id"], ["project_environments.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("environment_id", "revision"),
        **options,
    )
    op.create_table(
        "environment_revision_values",
        sa.Column("environment_id", sa.CHAR(36), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("category", sa.String(32), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("is_secret", sa.Boolean(), nullable=False),
        sa.Column("public_value", sa.JSON(none_as_null=True), nullable=True),
        sa.Column("secret_ref", sa.CHAR(36), nullable=True),
        sa.Column("ciphertext", sa.LargeBinary(), nullable=True),
        sa.Column("tag", sa.LargeBinary(16), nullable=True),
        sa.Column("nonce", sa.LargeBinary(12), nullable=True),
        sa.Column("wrapped_dek", sa.LargeBinary(), nullable=True),
        sa.Column("wrap_nonce", sa.LargeBinary(12), nullable=True),
        sa.Column("key_id", sa.String(255), nullable=True),
        sa.Column("algorithm", sa.String(32), nullable=True),
        sa.ForeignKeyConstraint(
            ["environment_id", "revision"],
            ["environment_revisions.environment_id", "environment_revisions.revision"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("secret_ref", name="uq_environment_values_secret_ref"),
        sa.CheckConstraint(
            "(is_secret = 1 AND public_value IS NULL AND secret_ref IS NOT NULL "
            "AND ciphertext IS NOT NULL AND tag IS NOT NULL AND nonce IS NOT NULL "
            "AND wrapped_dek IS NOT NULL AND wrap_nonce IS NOT NULL "
            "AND key_id IS NOT NULL AND algorithm IS NOT NULL) OR "
            "(is_secret = 0 AND secret_ref IS NULL AND ciphertext IS NULL "
            "AND tag IS NULL AND nonce IS NULL AND wrapped_dek IS NULL "
            "AND wrap_nonce IS NULL AND key_id IS NULL AND algorithm IS NULL)",
            name="ck_environment_values_secret_material",
        ),
        sa.PrimaryKeyConstraint("environment_id", "revision", "category", "name"),
        **options,
    )


def downgrade() -> None:
    op.drop_table("environment_revision_values")
    op.drop_table("environment_revisions")
    op.drop_index(
        "ix_environments_project_status", table_name="project_environments"
    )
    op.drop_table("project_environments")