"""路由决策表（B3）。

每条路由决策带理由 + 决策时画像快照，可事后解释（FR-004）。

Revision ID: 0003_route_decisions
Revises: 0002_ingest_documents
Create Date: 2026-11-01
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003_route_decisions"
down_revision = "0002_ingest_documents"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "route_decisions",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("doc_id", sa.Text(), nullable=False),
        sa.Column("level", sa.Text(), nullable=False),
        sa.Column("page_no", sa.Integer(), nullable=True),
        sa.Column("chain", sa.Text(), nullable=True),
        sa.Column("reasons", postgresql.JSONB(), nullable=False),
        sa.Column("profile_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_route_decisions_doc_id", "route_decisions", ["doc_id", "id"]
    )


def downgrade() -> None:
    op.drop_index("ix_route_decisions_doc_id", table_name="route_decisions")
    op.drop_table("route_decisions")
