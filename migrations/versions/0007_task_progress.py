"""任务进度表（observability / status）。

`TaskRunner` 低频写入（任务开始 / 每文档完成 / 任务结束），API/CLI 只读快照。
任务归属仍在队列与 PG 状态，不引入进程内状态。

Revision ID: 0007_task_progress
Revises: 0006_document_pages
Create Date: 2026-11-02
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0007_task_progress"
down_revision = "0006_document_pages"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "task_progress",
        sa.Column("task_id", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("progress", sa.Text(), nullable=False),
        sa.Column("pages_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("pages_done", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "detail", postgresql.JSONB(), nullable=False, server_default="{}"
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("task_id"),
    )
    op.create_index(
        "ix_task_progress_updated_at", "task_progress", ["updated_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_task_progress_updated_at", table_name="task_progress")
    op.drop_table("task_progress")
