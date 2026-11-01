"""文档输出页表（B7）。

交付产物按 (doc_id, page_no) upsert；缺口页同样落库（gap = true），不得跳过。

Revision ID: 0006_document_pages
Revises: 0005_page_reports
Create Date: 2026-11-01
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0006_document_pages"
down_revision = "0005_page_reports"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "document_pages",
        sa.Column("doc_id", sa.Text(), nullable=False),
        sa.Column("page_no", sa.Integer(), nullable=False),
        sa.Column("gap", sa.Boolean(), nullable=False),
        sa.Column("extract_quality", sa.Float(), nullable=True),
        sa.Column("markdown", sa.Text(), nullable=False),
        sa.Column("defect_codes", postgresql.JSONB(), nullable=False),
        sa.Column("delivery_level", sa.Text(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("doc_id", "page_no"),
    )


def downgrade() -> None:
    op.drop_table("document_pages")
