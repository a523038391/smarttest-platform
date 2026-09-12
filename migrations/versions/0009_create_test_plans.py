"""Create revisioned test plans, execution batches, and run specs.

Revision ID: 0009
Revises: 0008
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    options = {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"}
    op.create_table(
        "test_plans",
        sa.Column("id", sa.CHAR(36), primary_key=True),
        sa.Column("tenant_id", sa.CHAR(36), nullable=False),
        sa.Column("project_id", sa.CHAR(36), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("definition", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("current_revision", sa.Integer(), nullable=False),
        sa.Column("state_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        **options,
    )
    op.create_index(
        "ix_test_plans_project_status", "test_plans", ["project_id", "status"]
    )
    op.create_table(
        "test_plan_revisions",
        sa.Column("plan_id", sa.CHAR(36), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("definition", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["plan_id"], ["test_plans.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("plan_id", "revision"),
        **options,
    )
    op.create_table(
        "test_plan_execution_batches",
        sa.Column("id", sa.CHAR(36), primary_key=True),
        sa.Column("tenant_id", sa.CHAR(36), nullable=False),
        sa.Column("project_id", sa.CHAR(36), nullable=False),
        sa.Column("plan_id", sa.CHAR(36), nullable=False),
        sa.Column("plan_revision", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["plan_id", "plan_revision"],
            ["test_plan_revisions.plan_id", "test_plan_revisions.revision"],
        ),
        sa.UniqueConstraint(
            "tenant_id", "project_id", "idempotency_key",
            name="uq_plan_batches_scope_key",
        ),
        **options,
    )
    op.create_index(
        "ix_plan_batches_plan", "test_plan_execution_batches",
        ["plan_id", "plan_revision"],
    )
    op.create_table(
        "run_specs",
        sa.Column("id", sa.CHAR(36), primary_key=True),
        sa.Column("run_id", sa.CHAR(36), nullable=False),
        sa.Column("batch_id", sa.CHAR(36), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.CHAR(36), nullable=False),
        sa.Column("project_id", sa.CHAR(36), nullable=False),
        sa.Column("plan_id", sa.CHAR(36), nullable=False),
        sa.Column("plan_revision", sa.Integer(), nullable=False),
        sa.Column("item_id", sa.CHAR(36), nullable=False),
        sa.Column("row_key", sa.String(255), nullable=False),
        sa.Column("engine", sa.String(32), nullable=False),
        sa.Column("script_id", sa.CHAR(36), nullable=False),
        sa.Column("script_revision", sa.Integer(), nullable=False),
        sa.Column("source_ref", sa.String(512), nullable=False),
        sa.Column("content_digest", sa.CHAR(64), nullable=False),
        sa.Column("entrypoint", sa.String(512), nullable=False),
        sa.Column("timeout_seconds", sa.Integer(), nullable=False),
        sa.Column("environment_id", sa.CHAR(36), nullable=True),
        sa.Column("environment_revision", sa.Integer(), nullable=True),
        sa.Column("environment_variables", sa.JSON(), nullable=False),
        sa.Column("parameters", sa.JSON(), nullable=False),
        sa.Column("secret_references", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["test_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["batch_id"], ["test_plan_execution_batches.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["plan_id", "plan_revision"],
            ["test_plan_revisions.plan_id", "test_plan_revisions.revision"],
        ),
        sa.ForeignKeyConstraint(
            ["script_id", "script_revision"],
            ["automation_script_revisions.script_id", "automation_script_revisions.revision"],
        ),
        sa.ForeignKeyConstraint(
            ["environment_id", "environment_revision"],
            ["environment_revisions.environment_id", "environment_revisions.revision"],
        ),
        sa.UniqueConstraint("run_id", name="uq_run_specs_run_id"),
        sa.UniqueConstraint("batch_id", "position", name="uq_run_specs_batch_position"),
        sa.CheckConstraint(
            "(environment_id IS NULL AND environment_revision IS NULL) OR "
            "(environment_id IS NOT NULL AND environment_revision IS NOT NULL)",
            name="ck_run_specs_environment_pin",
        ),
        **options,
    )
    op.create_index("ix_run_specs_batch", "run_specs", ["batch_id"])


def downgrade() -> None:
    op.drop_index("ix_run_specs_batch", table_name="run_specs")
    op.drop_table("run_specs")
    op.drop_index("ix_plan_batches_plan", table_name="test_plan_execution_batches")
    op.drop_table("test_plan_execution_batches")
    op.drop_table("test_plan_revisions")
    op.drop_index("ix_test_plans_project_status", table_name="test_plans")
    op.drop_table("test_plans")