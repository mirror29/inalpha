"""accounts 表的读写 —— 用户级虚拟账户。

D-8b 起：每个用户（JWT sub）一份独立的 cash / initial_cash。
首次下单时 lazy create，默认 10000 USDT 起始资金。
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID

from psycopg import AsyncConnection

DEFAULT_INITIAL_CASH = Decimal("10000")


async def get_or_create(
    conn: AsyncConnection,
    account_id: UUID,
    *,
    initial_cash: Decimal = DEFAULT_INITIAL_CASH,
) -> dict[str, Any]:
    """按 account_id 查账户；不存在则按默认初始资金创建。

    幂等：UPSERT 走 ON CONFLICT DO NOTHING，并发首单不会重复初始化。
    返回最新的账户行（含 cash / initial_cash）。
    """
    async with conn.cursor() as cur:
        await cur.execute(
            """
            INSERT INTO accounts (account_id, initial_cash, cash)
            VALUES (%s, %s, %s)
            ON CONFLICT (account_id) DO NOTHING
            """,
            (str(account_id), initial_cash, initial_cash),
        )
        await cur.execute(
            "SELECT account_id, initial_cash, cash, created_at, updated_at "
            "FROM accounts WHERE account_id = %s",
            (str(account_id),),
        )
        row = await cur.fetchone()
    if row is None:  # 理论上不会
        raise RuntimeError(f"account {account_id} not found after upsert")
    return row  # type: ignore[return-value]


async def get(conn: AsyncConnection, account_id: UUID) -> dict[str, Any] | None:
    async with conn.cursor() as cur:
        await cur.execute(
            "SELECT account_id, initial_cash, cash, created_at, updated_at "
            "FROM accounts WHERE account_id = %s",
            (str(account_id),),
        )
        row = await cur.fetchone()
    return row  # type: ignore[return-value]


async def apply_cash_delta(
    conn: AsyncConnection,
    account_id: UUID,
    delta: Decimal,
) -> Decimal:
    """原子更新 cash += delta，返回新 cash。

    delta < 0 = 买单扣款；delta > 0 = 卖单入账。不做余额检查（D-8b 模拟盘允许"借"
    余额——回测和模拟盘不真扣实际资金；后续 D-9 接 risk 规则化时加 max_notional check）。
    """
    async with conn.cursor() as cur:
        await cur.execute(
            """
            UPDATE accounts
            SET cash = cash + %s, updated_at = NOW()
            WHERE account_id = %s
            RETURNING cash
            """,
            (delta, str(account_id)),
        )
        row = await cur.fetchone()
    if row is None:
        raise RuntimeError(f"account {account_id} not found when applying cash delta")
    return Decimal(row["cash"])  # type: ignore[index]
