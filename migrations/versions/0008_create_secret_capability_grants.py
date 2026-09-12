"""Create persisted one-time secret capability grants.

Revision ID: 0008
Revises: 0007
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    options = {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"}
    op.create_table(
        "secret_capability_grants",
        sa.Column("id", sa.CHAR(36), primary_key=True),
        sa.Column("token_digest", sa.CHAR(64), nullable=False),
        sa.Column("task_id", sa.CHAR(36), nullable=False),
        sa.Column("tenant_id", sa.CHAR(36), nullable=False),
        sa.Column("project_id", sa.CHAR(36), nullable=False),
        sa.Column("run_id", sa.CHAR(36), nullable=False),
        sa.Column("attempt_id", sa.CHAR(36), nullable=False),
        sa.Column("runner_id", sa.String(255), nullable=False),
        sa.Column("environment_id", sa.CHAR(36), nullable=False),
        sa.Column("environment_revision", sa.Integer(), nullable=False),
        sa.Column("reference_manifest", sa.JSON(), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["attempt_id"], ["run_attempts.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["environment_id", "environment_revision"],
            ["environment_revisions.environment_id", "environment_revisions.revision"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("token_digest", name="uq_secret_grants_token_digest"),
        sa.CheckConstraint("expires_at > issued_at", name="ck_secret_grants_expiry"),
        sa.CheckConstraint(
            "consumed_at IS NULL OR consumed_at >= issued_at",
            name="ck_secret_grants_consumed_at",
        ),
        sa.CheckConstraint(
            "revoked_at IS NULL OR revoked_at >= issued_at",
            name="ck_secret_grants_revoked_at",
        ),
        sa.CheckConstraint(
            "consumed_at IS NULL OR revoked_at IS NULL",
            name="ck_secret_grants_single_terminal_state",
        ),
        **options,
    )
    op.create_index("ix_secret_grants_attempt", "secret_capability_grants", ["attempt_id"])
    op.create_index("ix_secret_grants_expiry", "secret_capability_grants", ["expires_at"])
    op.create_index("ix_secret_grants_runner", "secret_capability_grants", ["runner_id"])


def downgrade() -> None:
    op.drop_index("ix_secret_grants_runner", table_name="secret_capability_grants")
    op.drop_index("ix_secret_grants_expiry", table_name="secret_capability_grants")
    op.drop_index("ix_secret_grants_attempt", table_name="secret_capability_grants")
    op.drop_table("secret_capability_grants")