"""orders 表读写。

orders 表 schema 见 migration 0001（基础） + 0002（扩 account/venue/symbol/fee/notional/trade_plan_id）。

行为约定：
- FILLED 和 REJECTED 都落盘（审计需要）
- ``client_order_id`` 由 ``OrderExecutor`` 生成（进程内自增），写库时作为 PK
- ``instrument_id`` 字段保留向后兼容（值 = "{symbol}@{venue}"），新代码用拆分的 venue/symbol
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from psycopg import AsyncConnection


async def insert(
    conn: AsyncConnection,
    *,
    account_id: UUID,
    client_order_id: str,
    venue: str,
    symbol: str,
    side: str,
    order_type: str,
    quantity: Decimal | float,
    price: Decimal | float | None,
    status: str,
    filled_quantity: Decimal | float,
    avg_fill_price: Decimal | float | None,
    fee: Decimal | float,
    notional: Decimal | float,
    ts_event: datetime,
    trade_plan_id: UUID | None = None,
) -> None:
    """写一行订单。status 可以是 FILLED / REJECTED / 等等（见 schema CHECK）。"""
    async with conn.cursor() as cur:
        await cur.execute(
            """
            INSERT INTO orders (
                client_order_id, instrument_id, side, type, quantity, price,
                status, filled_quantity, avg_fill_price, ts_event,
                account_id, venue, symbol, fee, notional, trade_plan_id
            ) VALUES (
                %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s
            )
            """,
            (
                client_order_id,
                f"{symbol}@{venue}",  # 老 instrument_id 字段保留
                side,
                order_type,
                quantity,
                price,
                status,
                filled_quantity,
                avg_fill_price,
                ts_event,
                str(account_id),
                venue,
                symbol,
                fee,
                notional,
                str(trade_plan_id) if trade_plan_id else None,
            ),
        )


async def set_realized_pnl(
    conn: AsyncConnection,
    *,
    client_order_id: str,
    realized_pnl: Decimal | float,
) -> None:
    """回写某订单的已实现盈亏（毛口径）。

    订单行在 fill 之前已插入（closed_trades 外键依赖其先在），盈亏要等 fill 落账后
    才算得出 —— 故 fill 之后单独 UPDATE 这一列。开仓/加仓单写 0，平/减仓单写该笔实现盈亏。
    与 insert 在同一事务里调用。
    """
    async with conn.cursor() as cur:
        await cur.execute(
            "UPDATE orders SET realized_pnl = %s WHERE client_order_id = %s",
            (realized_pnl, client_order_id),
        )


async def list_by_account(
    conn: AsyncConnection,
    account_id: UUID,
    *,
    symbol: str | None = None,
    status: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """列出某 account 的订单流水，按 ts_event DESC 排，最多 limit 条。"""
    sql = (
        "SELECT client_order_id, venue, symbol, side, type, quantity, price, "
        "status, filled_quantity, avg_fill_price, fee, notional, realized_pnl, "
        "ts_event, ts_init, trade_plan_id "
        "FROM orders WHERE account_id = %s"
    )
    params: list[Any] = [str(account_id)]
    if symbol is not None:
        sql += " AND symbol = %s"
        params.append(symbol)
    if status is not None:
        sql += " AND status = %s"
        params.append(status)
    sql += " ORDER BY ts_event DESC LIMIT %s"
    params.append(limit)

    async with conn.cursor() as cur:
        await cur.execute(sql, tuple(params))
        rows = await cur.fetchall()
    return list(rows)  # type: ignore[arg-type]


async def sum_fees(
    conn: AsyncConnection,
    *,
    account_id: UUID,
    venue: str,
    symbol: str,
    since: datetime,
) -> Decimal:
    """某 (account, venue, symbol) 自 ``since`` 起的累计手续费（**计价货币 / quote currency**）。

    live run ``cumulative_pnl`` 的净盈亏口径用此减项：``realized(毛) + unrealized(毛) - fees``。
    手续费在成交时已从 cash 桶扣（``fills.apply_fill_to_positions_and_cash``），但
    ``close_profit_abs`` / 未实现盈亏都是**毛口径**，不减费——展示盈亏因此对高频策略虚高
    （issue #45 follow-up）。这里把 run 期间手续费补回净盈亏。

    只统计 ``status='FILLED'``（REJECTED 单 fee=0，过滤更稳）；无单返 ``Decimal(0)``。
    与 ``closed_trades.sum_realized`` 同 run-scope 近似（同 symbol、``ts_event >= run.started_at``）。
    """
    async with conn.cursor() as cur:
        await cur.execute(
            "SELECT COALESCE(SUM(fee), 0) AS fees FROM orders "
            "WHERE account_id = %s AND venue = %s AND symbol = %s "
            "AND status = 'FILLED' AND ts_event >= %s",
            (str(account_id), venue, symbol, since),
        )
        row = await cur.fetchone()
    return Decimal(str(row["fees"])) if row else Decimal(0)  # type: ignore[index]
