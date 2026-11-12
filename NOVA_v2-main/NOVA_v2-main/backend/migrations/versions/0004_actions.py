"""assistant actions: action records on messages, approval requests without a project

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-18
"""
import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("messages") as batch:
        batch.add_column(sa.Column("actions", sa.JSON(), nullable=True))
    # Safety-critical device actions are not tied to a project.
    with op.batch_alter_table("action_requests") as batch:
        batch.alter_column("project_id", existing_type=sa.Integer(), nullable=True)


def downgrade() -> None:
    op.execute("DELETE FROM action_requests WHERE project_id IS NULL")
    with op.batch_alter_table("action_requests") as batch:
        batch.alter_column("project_id", existing_type=sa.Integer(), nullable=False)
    with op.batch_alter_table("messages") as batch:
        batch.drop_column("actions")
