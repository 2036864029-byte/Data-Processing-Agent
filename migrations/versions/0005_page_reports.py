"""质检报告表（B5）。

质检结果先落库再触发下游（G5）；按 (doc_id, page_no) upsert。

Revision ID: 0005_page_reports
Revises: 0004_page_profiles
Create Date: 2026-11-01
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0005_page_reports"
down_revision = "0004_page_profiles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "page_reports",
        sa.Column("doc_id", sa.Text(), nullable=False),
        sa.Column("page_no", sa.Integer(), nullable=False),
        sa.Column("verdict", sa.Text(), nullable=False),
        sa.Column("extract_quality", sa.Float(), nullable=False),
        sa.Column("defects", postgresql.JSONB(), nullable=False),
        sa.Column("attribution", sa.Text(), nullable=True),
        sa.Column("recommended_action", sa.Text(), nullable=True),
        sa.Column("qc_confidence", sa.Float(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("doc_id", "page_no"),
    )


def downgrade() -> None:
    op.drop_table("page_reports")
