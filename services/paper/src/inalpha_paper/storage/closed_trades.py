"""closed_trades 表 async CRUD（D-9 · ADR-0006 trade-based RiskRule 数据源）。

[migration 0007](../../../../infra/migrations/versions/0007_closed_trades.py) 定义表 schema。

设计约定（同其他 storage 模块）：
- 所有函数接 ``AsyncConnection`` 参数，让调用方控制事务
- 返回 ``dict[str, Any]`` (dict_row)

D-9.1a：写入路径已接入 HTTP 订单流
（``api/orders._apply_fill_to_positions_and_cash`` → ``positions.apply_fill``
检测平仓 → 同事务写入 ``closed_trades``）。
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from psycopg import AsyncConnection


async def insert_close(
    conn: AsyncConnection,
    *,
    account_id: UUID,
    venue: str,
    symbol: str,
    side: str,
    open_ts: datetime,
    close_ts: datetime,
    open_price: Decimal | float,
    close_price: Decimal | float,
    quantity: Decimal | float,
    close_profit_pct: float,
    close_profit_abs: float,
    exit_reason: str,
    open_order_id: str | None = None,
    close_order_id: str | None = None,
) -> int:
    """写一条 close trade。返回新 id。

    Args:
        side: ``'long'`` / ``'short'``（持仓方向，非订单 BUY/SELL）
        exit_reason: 必须在 CHECK 允许集合内（schema 强制）
    """
    async with conn.cursor() as cur:
        await cur.execute(
            """
            INSERT INTO closed_trades (
                account_id, venue, symbol, side,
                open_ts, close_ts, open_price, close_price, quantity,
                close_profit_pct, close_profit_abs, exit_reason,
                open_order_id, close_order_id
            ) VALUES (
                %s, %s, %s, %s,
                %s, %s, %s, %s, %s,
                %s, %s, %s,
                %s, %s
            )
            RETURNING id
            """,
            (
                str(account_id), venue, symbol, side,
                open_ts, close_ts, open_price, close_price, quantity,
                close_profit_pct, close_profit_abs, exit_reason,
                open_order_id, close_order_id,
            ),
        )
        row = await cur.fetchone()
    return int(row["id"])  # type: ignore[index]


async def list_recent(
    conn: AsyncConnection,
    *,
    account_id: UUID,
    close_after: datetime,
    close_before: datetime | None = None,
    venue: str | None = None,
    symbol: str | None = None,
    side: str | None = None,
    exit_reasons: list[str] | None = None,
    max_profit_pct: float | None = None,
    limit: int = 1000,
) -> list[dict[str, Any]]:
    """查时间窗内 close trades（喂给 RiskRule.TradeRepository）。

    所有参数与 `risk_rules.base.TradeRepository.get_closed_trades` 对齐。
    按 close_ts 升序（RiskRule.calculate_lock_end 取 max(close_ts)）。
    """
    sql = (
        "SELECT id, venue, symbol, side, open_ts, close_ts, "
        "open_price, close_price, quantity, "
        "close_profit_pct, close_profit_abs, exit_reason, "
        "open_order_id, close_order_id "
        "FROM closed_trades WHERE account_id = %s AND close_ts >= %s"
    )
    params: list[Any] = [str(account_id), close_after]
    if close_before is not None:
        sql += " AND close_ts < %s"
        params.append(close_before)
    if venue is not None:
        sql += " AND venue = %s"
        params.append(venue)
    if symbol is not None:
        sql += " AND symbol = %s"
        params.append(symbol)
    if side is not None and side != "*":
        sql += " AND side = %s"
        params.append(side)
    if exit_reasons is not None and len(exit_reasons) > 0:
        sql += " AND exit_reason = ANY(%s)"
        params.append(exit_reasons)
    if max_profit_pct is not None:
        sql += " AND close_profit_pct < %s"
        params.append(max_profit_pct)
    sql += " ORDER BY close_ts ASC LIMIT %s"
    params.append(limit)

    async with conn.cursor() as cur:
        await cur.execute(sql, tuple(params))
        rows = await cur.fetchall()
    return list(rows)  # type: ignore[arg-type]


async def sum_realized(
    conn: AsyncConnection,
    *,
    account_id: UUID,
    venue: str,
    symbol: str,
    since: datetime,
) -> Decimal:
    """某 (account, venue, symbol) 自 ``since`` 起的已实现盈亏合计（live run PnL 口径，issue #45）。

    返回 ``SUM(close_profit_abs)``（**计价货币 / quote currency**，毛盈亏不含手续费），
    无平仓返 ``Decimal(0)``。run-scope 近似按 (symbol, close_ts>=run.started_at) 取——
    同账户同 symbol 顺序跑多个 run 时归属可能重叠（已知限制，单 run 主路径准确）。
    """
    async with conn.cursor() as cur:
        await cur.execute(
            "SELECT COALESCE(SUM(close_profit_abs), 0) AS realized FROM closed_trades "
            "WHERE account_id = %s AND venue = %s AND symbol = %s AND close_ts >= %s",
            (str(account_id), venue, symbol, since),
        )
        row = await cur.fetchone()
    return Decimal(str(row["realized"])) if row else Decimal(0)  # type: ignore[index]


async def sum_realized_grouped(
    conn: AsyncConnection,
    *,
    account_id: UUID,
    since: datetime | None = None,
) -> list[dict[str, Any]]:
    """账户已实现盈亏按 (venue, symbol) 分组合计(账户快照口径)。

    ``since`` 传最近一次 reset 时刻:快照的 realized_pnl 以**成交审计源**
    (closed_trades)为准并按 reset epoch 起算——此前从 positions 行汇总,reset
    删行后快照凭空归零而 closed_trades 仍在,两套"已实现盈亏"口径分叉。
    ``since=None`` = 全历史(从未 reset 的账户)。
    """
    sql = (
        "SELECT venue, symbol, COALESCE(SUM(close_profit_abs), 0) AS realized "
        "FROM closed_trades WHERE account_id = %s"
    )
    args: list[Any] = [str(account_id)]
    if since is not None:
        sql += " AND close_ts > %s"
        args.append(since)
    sql += " GROUP BY venue, symbol"
    async with conn.cursor() as cur:
        await cur.execute(sql, tuple(args))
        rows = await cur.fetchall()
    return list(rows)  # type: ignore[arg-type]


async def count_by_account(
    conn: AsyncConnection,
    *,
    account_id: UUID,
    close_after: datetime,
) -> int:
    """统计窗口内 close trade 数量（监控 / debug 用）。"""
    async with conn.cursor() as cur:
        await cur.execute(
            "SELECT COUNT(*) AS cnt FROM closed_trades "
            "WHERE account_id = %s AND close_ts >= %s",
            (str(account_id), close_after),
        )
        row = await cur.fetchone()
    return int(row["cnt"])  # type: ignore[index]
