"""closed_trades 表（D-9 · ADR-0006 配套基础设施 · trade-based RiskRule 数据源）

Revision ID: 0007
Revises: 0006
Create Date: 2026-05-26

[ADR-0006 §D2](../../../docs/miro/decisions/0006-risk-rules.md) 5 件套中 4 个
trade-based rule（Cooldown / LowProfit / MaxDrawdown / StoplossGuard）需要"已平仓
trade 列表"。Inalpha 之前**没有显式 closed_trades 表**——positions 只存当前持仓 +
累计 realized_pnl，不存单笔 close 记录。

本 migration 加这张表的 **schema 基础设施**，但**不接入** Portfolio fill 路径
（那是独立任务 ADR-0007 候选，触及核心 fill 流程）。当前作用：

- 让 storage.closed_trades.insert_close + list_recent 等 async CRUD 可用
- 让 PostgresTradeRepository 实现可基于此表（async/sync 桥接独立处理）
- 为 ADR-0006 trade-based RiskRule 在生产场景真正生效铺路

字段设计：

- 一行 = 一次完整开-平交易（开仓 → 平仓的 P&L 闭环）
- ``close_profit_pct`` / ``close_profit_abs`` 直接喂给 RiskRule（不需要 Python 计算）
- ``exit_reason`` CHECK 限制：StoplossGuardRule 看其中 'stop_loss' / 'trailing_stop_loss' /
  'liquidation' 三种
- 无外键到 orders 表：``open_order_id`` / ``close_order_id`` 软引用，让 close_trades
  独立可读（订单流水可能因审计裁剪）
"""
from __future__ import annotations

from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE closed_trades (
            id                  BIGSERIAL PRIMARY KEY,
            account_id          UUID NOT NULL,
            venue               VARCHAR(64) NOT NULL,
            symbol              VARCHAR(128) NOT NULL,
            side                VARCHAR(8) NOT NULL
                                CHECK (side IN ('long', 'short')),
            open_ts             TIMESTAMPTZ NOT NULL,
            close_ts            TIMESTAMPTZ NOT NULL,
            open_price          NUMERIC(24, 8) NOT NULL,
            close_price         NUMERIC(24, 8) NOT NULL,
            quantity            NUMERIC(24, 8) NOT NULL,
            close_profit_pct    DOUBLE PRECISION NOT NULL,
            close_profit_abs    DOUBLE PRECISION NOT NULL,
            exit_reason         VARCHAR(32) NOT NULL
                                CHECK (exit_reason IN (
                                    'stop_loss',
                                    'trailing_stop_loss',
                                    'liquidation',
                                    'take_profit',
                                    'manual',
                                    'signal'
                                )),
            open_order_id       VARCHAR(64),
            close_order_id      VARCHAR(64),
            created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute(
        "CREATE INDEX closed_trades_account_close_ts_idx "
        "ON closed_trades (account_id, close_ts DESC)"
    )
    op.execute(
        "CREATE INDEX closed_trades_symbol_close_ts_idx "
        "ON closed_trades (account_id, venue, symbol, close_ts DESC)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS closed_trades_symbol_close_ts_idx")
    op.execute("DROP INDEX IF EXISTS closed_trades_account_close_ts_idx")
    op.execute("DROP TABLE IF EXISTS closed_trades")
