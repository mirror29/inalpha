"""backtest_trades 表读写 —— 回测逐笔成交（含每笔实现盈亏）。

由 ``runner.run_backtest`` 在落 ``backtest_runs`` 拿到 run_id 后批量写入
``BacktestReport.fills``；策略详情页经 ``GET /backtest_runs/{id}/trades`` 读出复盘。

参考 ``backtest_runs.py`` / ``orders.py`` 的事务约定：调用方持有 AsyncConnection，
本模块只发 SQL；cursor 行工厂为 dict_row（与其它 paper storage 一致）。
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from psycopg import AsyncConnection

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ..engine.report import FillRecord


def _ns_to_dt(ts_ns: int) -> datetime:
    """纳秒整数 → tz-aware datetime（落 TIMESTAMPTZ 用）。"""
    return datetime.fromtimestamp(ts_ns / 1_000_000_000, tz=UTC)


async def insert_fills(
    conn: AsyncConnection,
    run_id: UUID,
    fills: Sequence[FillRecord],
) -> int:
    """批量写一个回测的逐笔成交，返回写入行数。

    ``seq`` 用 fills 的下标（成交先后顺序）。fills 为空时直接返 0（不发 SQL）。
    """
    if not fills:
        return 0
    rows = [
        (
            str(run_id),
            seq,
            _ns_to_dt(f.ts_ns),
            f.bar_close,
            f.side,
            f.quantity,
            f.order_type,
            f.fill_price,
            f.fee,
            f.realized_pnl,
            f.intent,
            f.tag,
        )
        for seq, f in enumerate(fills)
    ]
    async with conn.cursor() as cur:
        await cur.executemany(
            """
            INSERT INTO backtest_trades (
                backtest_run_id, seq, bar_ts, bar_close, side, quantity,
                order_type, fill_price, fee, realized_pnl, intent, tag
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            rows,
        )
    return len(rows)


async def list_by_run(
    conn: AsyncConnection,
    run_id: UUID,
    *,
    limit: int = 500,
) -> list[dict[str, Any]]:
    """按 backtest_run_id 拉逐笔成交（按 seq 升序 = 成交先后）。"""
    async with conn.cursor() as cur:
        await cur.execute(
            """
            SELECT id, seq, bar_ts, bar_close, side, quantity, order_type,
                   fill_price, fee, realized_pnl, intent, tag, created_at
            FROM backtest_trades
            WHERE backtest_run_id = %s
            ORDER BY seq ASC
            LIMIT %s
            """,
            (str(run_id), limit),
        )
        rows = await cur.fetchall()
    return [
        {
            "id": r["id"],
            "seq": r["seq"],
            "bar_ts": r["bar_ts"],
            "bar_close": r["bar_close"],
            "side": r["side"],
            "quantity": r["quantity"],
            "order_type": r["order_type"],
            "fill_price": r["fill_price"],
            "fee": r["fee"],
            "realized_pnl": r["realized_pnl"],
            "intent": r["intent"],
            "tag": r["tag"],
            "created_at": r["created_at"],
        }
        for r in rows
    ]
