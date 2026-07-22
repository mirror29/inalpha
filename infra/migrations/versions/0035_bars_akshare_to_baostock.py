"""将旧 A 股 bars venue 从 ``akshare`` 迁移到 ``baostock``。

迁移使用 upsert 后删除旧行，避免目标 venue 已有同键数据时违反主键约束。
"""
from __future__ import annotations

from alembic import op

revision: str = "0035"
down_revision: str | None = "0034"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO bars (ts, venue, symbol, timeframe, open, high, low, close, volume)
        SELECT ts, 'baostock', symbol, timeframe, open, high, low, close, volume
        FROM bars
        WHERE venue = 'akshare' AND symbol ~ '^(sh|sz)\\.'
        ON CONFLICT (ts, venue, symbol, timeframe) DO UPDATE
        SET open = EXCLUDED.open,
            high = EXCLUDED.high,
            low = EXCLUDED.low,
            close = EXCLUDED.close,
            volume = EXCLUDED.volume
        """
    )
    op.execute(
        "DELETE FROM bars WHERE venue = 'akshare' AND symbol ~ '^(sh|sz)\\.'"
    )


def downgrade() -> None:
    op.execute(
        """
        INSERT INTO bars (ts, venue, symbol, timeframe, open, high, low, close, volume)
        SELECT ts, 'akshare', symbol, timeframe, open, high, low, close, volume
        FROM bars
        WHERE venue = 'baostock' AND symbol ~ '^(sh|sz)\\.'
        ON CONFLICT (ts, venue, symbol, timeframe) DO UPDATE
        SET open = EXCLUDED.open,
            high = EXCLUDED.high,
            low = EXCLUDED.low,
            close = EXCLUDED.close,
            volume = EXCLUDED.volume
        """
    )
    op.execute(
        "DELETE FROM bars WHERE venue = 'baostock' AND symbol ~ '^(sh|sz)\\.'"
    )
