"""Add immutable execution, retry, and plan concurrency policies.

Revision ID: 0010
Revises: 0009
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "test_plans",
        sa.Column("max_parallel", sa.Integer(), nullable=False, server_default="10"),
    )
    op.add_column(
        "test_plan_revisions",
        sa.Column("max_parallel", sa.Integer(), nullable=False, server_default="10"),
    )
    op.add_column(
        "test_plan_execution_batches",
        sa.Column("max_parallel", sa.Integer(), nullable=False, server_default="10"),
    )
    op.add_column(
        "run_specs",
        sa.Column("execution_policy", sa.JSON(), nullable=True),
    )
    op.execute("UPDATE run_specs SET execution_policy = '{}' WHERE execution_policy IS NULL")
    with op.batch_alter_table("run_specs") as batch_op:
        batch_op.alter_column(
            "execution_policy", existing_type=sa.JSON(), nullable=False,
        )
    for table_name in (
        "test_plans", "test_plan_revisions", "test_plan_execution_batches",
    ):
        with op.batch_alter_table(table_name) as batch_op:
            batch_op.alter_column(
                "max_parallel", existing_type=sa.Integer(),
                nullable=False, server_default=None,
            )
            batch_op.create_check_constraint(
                {
                    "test_plans": "ck_test_plans_max_parallel",
                    "test_plan_revisions": "ck_test_plan_revisions_max_parallel",
                    "test_plan_execution_batches": "ck_plan_batches_max_parallel",
                }[table_name],
                "max_parallel BETWEEN 1 AND 100",
            )


def downgrade() -> None:
    constraints = {
        "test_plan_execution_batches": "ck_plan_batches_max_parallel",
        "test_plan_revisions": "ck_test_plan_revisions_max_parallel",
        "test_plans": "ck_test_plans_max_parallel",
    }
    for table_name, constraint_name in constraints.items():
        with op.batch_alter_table(table_name) as batch_op:
            batch_op.drop_constraint(constraint_name, type_="check")
            batch_op.drop_column("max_parallel")
    with op.batch_alter_table("run_specs") as batch_op:
        batch_op.drop_column("execution_policy")