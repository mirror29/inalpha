"""规范化 A 股 bars 的历史 symbol 格式。

兼容期曾接受 ``SH.600519``、``600519.SH`` 等形式。venue 已统一为 ``baostock`` 后，
这些 symbol 仍会形成不同的持久化 namespace；本迁移将其合并为 ``sh.600519`` / ``sz.000001``。
"""

from __future__ import annotations

from alembic import op

revision: str = "0036"
down_revision: str | None = "0035"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None

_PREFIX_SYMBOL_PATTERN = "^(sh|sz)[.][0-9][0-9][0-9][0-9][0-9][0-9]$"
_SUFFIX_SYMBOL_PATTERN = "^[0-9][0-9][0-9][0-9][0-9][0-9][.](sh|sz)$"

_CANONICAL_SYMBOL_SQL = f"""
CASE
    WHEN btrim(symbol) ~* '{_PREFIX_SYMBOL_PATTERN}' THEN
        lower(split_part(btrim(symbol), '.', 1)) || '.' ||
        split_part(btrim(symbol), '.', 2)
    WHEN btrim(symbol) ~* '{_SUFFIX_SYMBOL_PATTERN}' THEN
        lower(substring(btrim(symbol) from '\\.([^.]+)$')) || '.' ||
        regexp_replace(btrim(symbol), '\\.(sh|sz)$', '', 'i')
END
"""


def upgrade() -> None:
    """把旧 venue / symbol 组合合并到 canonical Baostock identity。"""
    op.execute(
        f"""
        INSERT INTO bars (ts, venue, symbol, timeframe, open, high, low, close, volume)
        SELECT DISTINCT ON (ts, canonical_symbol, timeframe)
            ts,
            'baostock',
            canonical_symbol,
            timeframe,
            open,
            high,
            low,
            close,
            volume
        FROM (
            SELECT
                ts,
                venue,
                symbol,
                timeframe,
                open,
                high,
                low,
                close,
                volume,
                {_CANONICAL_SYMBOL_SQL} AS canonical_symbol
            FROM bars
            WHERE venue IN ('akshare', 'baostock')
              AND (
                  btrim(symbol) ~* '{_PREFIX_SYMBOL_PATTERN}'
                  OR btrim(symbol) ~* '{_SUFFIX_SYMBOL_PATTERN}'
              )
        ) AS candidates
        WHERE canonical_symbol IS NOT NULL
          AND NOT (venue = 'baostock' AND symbol = canonical_symbol)
        ORDER BY
            ts,
            canonical_symbol,
            timeframe,
            CASE WHEN venue = 'baostock' THEN 0 ELSE 1 END,
            symbol
        ON CONFLICT (ts, venue, symbol, timeframe) DO NOTHING
        """
    )
    op.execute(
        f"""
        DELETE FROM bars
        WHERE venue IN ('akshare', 'baostock')
          AND (
              btrim(symbol) ~* '{_PREFIX_SYMBOL_PATTERN}'
              OR btrim(symbol) ~* '{_SUFFIX_SYMBOL_PATTERN}'
          )
          AND NOT (
              venue = 'baostock'
              AND symbol = ({_CANONICAL_SYMBOL_SQL})
          )
        """
    )


def downgrade() -> None:
    """symbol 原始大小写/前后缀不可无损恢复，降级保持 canonical 数据。"""
