"""positions 表添加 session_realized_pnl 字段。

持仓记录的已实现盈亏口径优化：
- realized_pnl: 全历史累计已实现盈亏（保留原有语义）
- session_realized_pnl: 当前持仓相关的已实现盈亏（新增）
  - 开仓时清零
  - 平仓时累加
  - 完全平仓后重新开仓时再次清零

这样用户可以：
- 在持仓表看到"当前持仓相关的收益"
- 在累计净盈亏看到"总收益"
"""

from alembic import op


def upgrade() -> None:
    op.execute(
        "ALTER TABLE positions ADD COLUMN IF NOT EXISTS session_realized_pnl "
        "NUMERIC NOT NULL DEFAULT 0"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE positions DROP COLUMN IF EXISTS session_realized_pnl")