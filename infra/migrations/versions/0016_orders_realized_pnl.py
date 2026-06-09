"""orders 增 realized_pnl：每笔成交记录已实现盈亏

Revision ID: 0016
Revises: 0015
Create Date: 2026-06-09

需求：模拟盘每一笔交易记录都要带这笔的盈亏，便于用户逐笔回溯。

口径（与 position.realized_pnl / closed_trades.close_profit_abs 一致）：
- **平/减仓单** = 这笔平掉部分的已实现盈亏（毛口径，不减手续费）
  ``(fill_price - avg_open_price) * closed_qty * sign``
- **开/加仓单** = 0（尚未实现）
- **未成交单（REJECTED 等）** = NULL（无成交无盈亏）

写入侧：``fills.apply_fill_to_positions_and_cash`` 返回该笔实现盈亏，``api/orders`` 与
``api/trade_plans`` 在 fill 落账后 ``orders_store.set_realized_pnl`` 回写本列（订单行需先于
closed_trades 插入，故盈亏算出后单独 UPDATE）。历史订单留 NULL（迁移不回算，UI 显 “—”）。
"""
from __future__ import annotations

from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    # 可空：历史订单不回算、未成交单无盈亏 —— 都留 NULL，UI 显 “—”。
    op.execute("ALTER TABLE orders ADD COLUMN realized_pnl NUMERIC")


def downgrade() -> None:
    op.execute("ALTER TABLE orders DROP COLUMN realized_pnl")
