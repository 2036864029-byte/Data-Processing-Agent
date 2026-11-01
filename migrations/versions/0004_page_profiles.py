"""页面画像表（B4）。

两段写入：路由段（class / layout）先写，质量段（signals / quality）解析后写。
route_reason 原样保存在 class JSONB 内，不裁剪 / 不映射。

Revision ID: 0004_page_profiles
Revises: 0003_route_decisions
Create Date: 2026-11-01
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0004_page_profiles"
down_revision = "0003_route_decisions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "page_profiles",
        sa.Column("doc_id", sa.Text(), nullable=False),
        sa.Column("page_no", sa.Integer(), nullable=False),
        sa.Column("class", postgresql.JSONB(), nullable=False),
        sa.Column("layout", postgresql.JSONB(), nullable=False),
        sa.Column("signals", postgresql.JSONB(), nullable=True),
        sa.Column("quality", postgresql.JSONB(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("doc_id", "page_no"),
    )


def downgrade() -> None:
    op.drop_table("page_profiles")
