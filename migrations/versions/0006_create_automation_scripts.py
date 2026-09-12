"""Create versioned automation scripts and test case links.

Revision ID: 0006
Revises: 0005
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    options = {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"}
    op.create_table(
        "automation_scripts",
        sa.Column("id", sa.CHAR(36), primary_key=True),
        sa.Column("project_id", sa.CHAR(36), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("engine", sa.String(32), nullable=False),
        sa.Column("entrypoint", sa.String(512), nullable=False),
        sa.Column("source_ref", sa.String(512), nullable=False),
        sa.Column("content_digest", sa.CHAR(64), nullable=False),
        sa.Column("timeout_seconds", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("current_revision", sa.Integer(), nullable=False),
        sa.Column("state_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        **options,
    )
    op.create_index(
        "ix_automation_scripts_project_status",
        "automation_scripts", ["project_id", "status"],
    )
    op.create_table(
        "automation_script_revisions",
        sa.Column("script_id", sa.CHAR(36), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("entrypoint", sa.String(512), nullable=False),
        sa.Column("source_ref", sa.String(512), nullable=False),
        sa.Column("content_digest", sa.CHAR(64), nullable=False),
        sa.Column("timeout_seconds", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["script_id"], ["automation_scripts.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("script_id", "revision"),
        **options,
    )
    op.create_table(
        "test_case_script_links",
        sa.Column("test_case_id", sa.CHAR(36), nullable=False),
        sa.Column("script_id", sa.CHAR(36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["test_case_id"], ["test_cases.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["script_id"], ["automation_scripts.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("test_case_id", "script_id"),
        **options,
    )
    op.create_index(
        "ix_test_case_script_links_script", "test_case_script_links", ["script_id"]
    )


def downgrade() -> None:
    op.drop_index(
        "ix_test_case_script_links_script", table_name="test_case_script_links"
    )
    op.drop_table("test_case_script_links")
    op.drop_table("automation_script_revisions")
    op.drop_index(
        "ix_automation_scripts_project_status", table_name="automation_scripts"
    )
    op.drop_table("automation_scripts")