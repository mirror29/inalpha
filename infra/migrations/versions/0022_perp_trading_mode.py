"""perp_trading_mode —— 永续做空/合约杠杆 v1 schema(加列,向后兼容)

Revision ID: 0022
Revises: 0021
Create Date: 2026-06-25

USDT-M 永续 + 逐仓 + 单向的 v1 落库基础。全部**加列且带默认值**,旧行/旧代码不受影响
(未开永续的 run 恒 trading_mode='spot' / leverage=1,行为与现状一致):

- ``strategy_runs``:per-run 配置 ``trading_mode``(spot|perp)、``margin_mode``
  (isolated|cross,v1 强制 isolated)、``leverage``(>=1)。
- ``positions``:``leverage``、``margin_used``(逐仓占用保证金)、``liquidation_price``
  (开仓即算的强平价,可空 = spot 无强平)。
- ``orders``:落单时的 ``trading_mode`` / ``leverage`` 留痕(复盘 / 审计)。
"""
from __future__ import annotations

from alembic import op

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE strategy_runs
            ADD COLUMN IF NOT EXISTS trading_mode TEXT NOT NULL DEFAULT 'spot'
                CHECK (trading_mode IN ('spot', 'perp')),
            ADD COLUMN IF NOT EXISTS margin_mode TEXT NOT NULL DEFAULT 'isolated'
                CHECK (margin_mode IN ('isolated', 'cross')),
            ADD COLUMN IF NOT EXISTS leverage INT NOT NULL DEFAULT 1
                CHECK (leverage >= 1)
        """
    )
    op.execute(
        """
        ALTER TABLE positions
            ADD COLUMN IF NOT EXISTS leverage INT NOT NULL DEFAULT 1,
            ADD COLUMN IF NOT EXISTS margin_used NUMERIC NOT NULL DEFAULT 0,
            ADD COLUMN IF NOT EXISTS liquidation_price NUMERIC
        """
    )
    op.execute(
        """
        ALTER TABLE orders
            ADD COLUMN IF NOT EXISTS trading_mode TEXT NOT NULL DEFAULT 'spot',
            ADD COLUMN IF NOT EXISTS leverage INT NOT NULL DEFAULT 1
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE strategy_runs
            DROP COLUMN IF EXISTS trading_mode,
            DROP COLUMN IF EXISTS margin_mode,
            DROP COLUMN IF EXISTS leverage
        """
    )
    op.execute(
        """
        ALTER TABLE positions
            DROP COLUMN IF EXISTS leverage,
            DROP COLUMN IF EXISTS margin_used,
            DROP COLUMN IF EXISTS liquidation_price
        """
    )
    op.execute(
        """
        ALTER TABLE orders
            DROP COLUMN IF EXISTS trading_mode,
            DROP COLUMN IF EXISTS leverage
        """
    )
