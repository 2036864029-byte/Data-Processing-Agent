"""基线空迁移

P0-A 阶段无业务表（A4 仅建迁移骨架）；业务模型随 P0-B 落库，
所有 DDL 一律在此目录手写迁移（宪法 G9：所有 DDL 走迁移）。

Revision ID: 0001_baseline
Revises:
Create Date: 2026-10-31
"""

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """基线：无 DDL；alembic_version 表由 Alembic 基础设施自动创建。"""


def downgrade() -> None:
    """回滚：无 DDL；alembic_version 表由 Alembic 基础设施自动移除。"""
