"""strategy_run_decisions 增加 closed_profit_abs / closed_profit_pct 列。

Revision ID: 0031
Revises: 0030
Create Date: 2026-07-08

平/减仓产生的已实现盈亏写入决策时间线,供复盘面板显示本次平仓收益。
closed_profit_abs 为毛口径(不含手续费),与 closed_trades.close_profit_abs 同口径。
"""
from __future__ import annotations

from alembic import op

revision: str = "0031"
down_revision: str | None = "0030"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE strategy_run_decisions
        ADD COLUMN IF NOT EXISTS closed_profit_abs numeric,
        ADD COLUMN IF NOT EXISTS closed_profit_pct numeric;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE strategy_run_decisions
        DROP COLUMN IF EXISTS closed_profit_abs,
        DROP COLUMN IF EXISTS closed_profit_pct;
        """
    )
