"""positions 表加 ts_opened + open_order_id 列（D-9.1a 收口 · closed_trades 写入链路）

Revision ID: 0008
Revises: 0007
Create Date: 2026-05-28

[issue #8] trade-based RiskRule（Cooldown / LowProfit / MaxDrawdown /
StoplossGuard）当前在 HTTP 路径无法触发，因为 ``closed_trades`` 表永远为空
（写入链路未接入 HTTP 订单流）。

本 migration 给 positions 表加两列，让 ``storage.positions.apply_fill()``
在开仓时记录 ts_opened / open_order_id，平仓时就能构造完整的
``closed_trades`` 行（open_ts / open_price / open_order_id + close_ts /
close_price / close_order_id）。

设计：
- ``ts_opened``：最近一次从 flat → 开仓的 UTC 时刻（NULL = 从未开过仓）
- ``open_order_id``：开仓时的 client_order_id（软引用，NULL = 从未开过仓）
- 反向减仓未平时这两列不变（仍是原开仓信息）
- 反向开新仓（跨过 0）时更新为新开仓信息
"""
from __future__ import annotations

from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE positions ADD COLUMN IF NOT EXISTS ts_opened TIMESTAMPTZ"
    )
    op.execute(
        "ALTER TABLE positions ADD COLUMN IF NOT EXISTS open_order_id VARCHAR(64)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS positions_ts_opened_idx "
        "ON positions (account_id, ts_opened DESC) WHERE ts_opened IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS positions_ts_opened_idx")
    op.execute("ALTER TABLE positions DROP COLUMN IF EXISTS open_order_id")
    op.execute("ALTER TABLE positions DROP COLUMN IF EXISTS ts_opened")
