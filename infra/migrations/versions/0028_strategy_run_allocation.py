"""strategy_run_allocation ext —— CR #131 修复收口

Revision ID: 0028
Revises: 0027
Create Date: 2026-07-03

buying-power 分支 CR #131 两轮修复（锁内零 HTTP + 老仓保证金聚合 + reset
纪元三口径收口）。原为 parallel chain 的 0026,现串入单链续接 0027。
"""
from __future__ import annotations

from alembic import op

revision: str = "0028"
down_revision: str | None = "0027"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    """CR #131 加固：allocation 列已由 0027 创建，本迁移为占位标记。"""
    pass


def downgrade() -> None:
    pass
