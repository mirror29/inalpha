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
        "status, filled_quantity, avg_fill_price, fee, notional, "
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
