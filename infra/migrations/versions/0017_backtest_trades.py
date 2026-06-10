"""backtest_trades —— 回测逐笔成交（含每笔实现盈亏，D-11+ 详情页「回测成交」）

Revision ID: 0017
Revises: 0016
Create Date: 2026-06-09

回测引擎此前只把 ``num_trades`` 计数带回 + equity_curve（不落库），逐笔成交只存在于
live run 的 ``strategy_run_decisions``。本表让 **回测** 的每笔成交也可复盘：策略详情页
``/lab/[id]`` 展示该候选最近一次回测的逐笔买卖 + 每笔实现盈亏。

每笔成交一行（由 ``Portfolio._handle_fill`` 收集 → ``BacktestReport.fills`` 带回主进程 →
``runner`` 落本表）。``realized_pnl`` 是本笔引起的持仓 realized_pnl 增量（开仓笔=0，
平仓/反手笔=价差盈亏，不含手续费，与 ``Portfolio.closed_trade_pnls`` 同口径；fee 单列）。
``backtest_run_id`` 外键级联：删 backtest_runs 行时一并清掉其 trades。
"""
from __future__ import annotations

from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE backtest_trades (
            id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            backtest_run_id  UUID NOT NULL
                REFERENCES backtest_runs(id) ON DELETE CASCADE,
            seq              INTEGER NOT NULL,
            bar_ts           TIMESTAMPTZ NOT NULL,
            bar_close        NUMERIC NOT NULL,
            side             TEXT NOT NULL,
            quantity         NUMERIC NOT NULL,
            order_type       TEXT NOT NULL,
            fill_price       NUMERIC,
            fee              NUMERIC,
            realized_pnl     NUMERIC,
            intent           TEXT
                CHECK (intent IN ('open_long', 'open_short', 'close')),
            tag              TEXT,
            created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    # UNIQUE:同一 run 的 seq 不可重复 —— insert_fills 被重试/重复调用时由约束层
    # 拒绝,否则静默双份成交记录会被 UI 当真实交易展示。
    op.execute(
        "CREATE UNIQUE INDEX backtest_trades_run_idx "
        "ON backtest_trades (backtest_run_id, seq)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS backtest_trades_run_idx")
    op.execute("DROP TABLE IF EXISTS backtest_trades")
