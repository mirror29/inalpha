"""strategy_run_decisions —— live runner 每根 bar 的决策复盘日志（D-11 issue #1）

Revision ID: 0011
Revises: 0010
Create Date: 2026-06-02

让用户能**复盘** live runner 跑模拟盘时"每根 bar 策略做了什么决定、结果如何"。
每次 ``on_bar`` 产生下单意图时记一行：bar 时点 / 价、订单意图（side / qty / 类型 /
strategy 的 tag）、撮合结果（filled / rejected / risk_rejected）、成交价、以及
``plan_id`` / ``order_id`` 供交叉引用 ``trade_plans``（rationale）/ ``closed_trades``（盈亏）。

注：这是**决策事件流**（仅在策略产生订单的 bar 上记一行），不是逐 bar 全量快照。
确定性策略"代码即理由"，所以记可观测的 {市场上下文 + 订单 + 结果}；strategy 若想表达
语义意图可通过 ``Order.tag`` 透传（如 'stop_loss' / 'take_profit'），这里落 ``tag`` 列。
"""
from __future__ import annotations

from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE strategy_run_decisions (
            id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            run_id       UUID NOT NULL
                REFERENCES strategy_runs(id) ON DELETE CASCADE,
            bar_ts       TIMESTAMPTZ NOT NULL,
            bar_close    NUMERIC NOT NULL,
            side         TEXT NOT NULL,
            quantity     NUMERIC NOT NULL,
            order_type   TEXT NOT NULL,
            limit_price  NUMERIC,
            tag          TEXT,
            outcome      TEXT NOT NULL
                CHECK (outcome IN ('filled', 'rejected', 'risk_rejected')),
            fill_price   NUMERIC,
            fee          NUMERIC,
            plan_id      UUID,
            order_id     TEXT,
            reason       TEXT,
            created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute(
        "CREATE INDEX strategy_run_decisions_run_idx "
        "ON strategy_run_decisions (run_id, created_at)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS strategy_run_decisions_run_idx")
    op.execute("DROP TABLE IF EXISTS strategy_run_decisions")
