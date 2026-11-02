"""任务状态表（状态机 + 续跑点 + 已完成文档结果）。

runner 每个文档结束后提交 resume_point 与该文档结果；重启后跳过已完成文档。
状态与结果都在 PG，进程内不持有。

Revision ID: 0008_task_state
Revises: 0007_task_progress
Create Date: 2026-11-02
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0008_task_state"
down_revision = "0007_task_progress"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "task_state",
        sa.Column("task_id", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("resume_point", sa.Integer(), nullable=False, server_default="-1"),
        sa.Column(
            "input_signature", sa.Text(), nullable=False, server_default=""
        ),
        sa.Column(
            "outcomes", postgresql.JSONB(), nullable=False, server_default="[]"
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("task_id"),
    )


def downgrade() -> None:
    op.drop_table("task_state")
