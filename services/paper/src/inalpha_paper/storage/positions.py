"""positions 表读写 + 持仓累计逻辑。

D-8b 起：每个用户 + venue + symbol 一行；每根 fill 后 reduce 更新。

持仓累计规则（参考 [model/positions.py](../model/positions.py) 的内核 Position 实现，
SQL 化）：

- 同向加仓：``new_qty = qty + fill_qty``，``new_avg = (qty*avg + fill_qty*fill_price) / new_qty``
- 反向减仓未平：``new_qty = qty - fill_qty``，avg 不变，``realized_pnl += (fill_price - avg) * fill_qty * sign``
- 反向减仓平到 0：清空 qty / avg = 0，realized_pnl 累加
- 反向减仓平后反向开仓：先平，剩余再以 ``fill_price`` 开新方向，generation+1

为了简化（D-8b MVP），所有计算放在 Python 里，DB 操作只做"读最新行 + UPSERT 新值"。
后续真要并发安全可以加 SELECT FOR UPDATE 锁行。
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID

from psycopg import AsyncConnection


async def apply_fill(
    conn: AsyncConnection,
    *,
    account_id: UUID,
    venue: str,
    symbol: str,
    side: str,  # 'BUY' | 'SELL'
    fill_qty: Decimal,
    fill_price: Decimal,
) -> dict[str, Any]:
    """一笔 fill 应用到 positions 表。返回更新后的持仓行。

    流程：SELECT 当前行 → Python 算新值 → UPSERT 回去。**调用方必须包在事务里**。
    """
    # 1. 读当前持仓（不存在视为 flat）
    async with conn.cursor() as cur:
        await cur.execute(
            "SELECT quantity, avg_open_price, realized_pnl, generation "
            "FROM positions WHERE account_id = %s AND venue = %s AND symbol = %s "
            "FOR UPDATE",
            (str(account_id), venue, symbol),
        )
        row = await cur.fetchone()

    cur_qty = Decimal(row["quantity"]) if row else Decimal(0)  # type: ignore[index]
    cur_avg = Decimal(row["avg_open_price"]) if row else Decimal(0)  # type: ignore[index]
    cur_pnl = Decimal(row["realized_pnl"]) if row else Decimal(0)  # type: ignore[index]
    generation = int(row["generation"]) if row else 0  # type: ignore[index]

    # 2. 应用 fill
    signed_fill = fill_qty if side == "BUY" else -fill_qty
    new_qty, new_avg, new_pnl, new_gen = _reduce_position(
        cur_qty, cur_avg, cur_pnl, generation, signed_fill, fill_price
    )

    # 3. UPSERT
    async with conn.cursor() as cur:
        await cur.execute(
            """
            INSERT INTO positions (
                account_id, venue, symbol, quantity, avg_open_price,
                realized_pnl, generation, updated_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
            ON CONFLICT (account_id, venue, symbol) DO UPDATE
              SET quantity       = EXCLUDED.quantity,
                  avg_open_price = EXCLUDED.avg_open_price,
                  realized_pnl   = EXCLUDED.realized_pnl,
                  generation     = EXCLUDED.generation,
                  updated_at     = NOW()
            RETURNING account_id, venue, symbol, quantity, avg_open_price,
                      realized_pnl, generation, updated_at
            """,
            (str(account_id), venue, symbol, new_qty, new_avg, new_pnl, new_gen),
        )
        new_row = await cur.fetchone()
    return new_row  # type: ignore[return-value]


def _reduce_position(
    cur_qty: Decimal,
    cur_avg: Decimal,
    cur_pnl: Decimal,
    generation: int,
    signed_fill: Decimal,  # 正 = BUY, 负 = SELL
    fill_price: Decimal,
) -> tuple[Decimal, Decimal, Decimal, int]:
    """纯函数：返回 (new_qty, new_avg, new_pnl, new_generation)。

    覆盖 4 种情形：同向加 / 反向减仓 / 反向平仓 / 反向超平开反单。
    """
    if cur_qty == 0:
        # flat → open new
        return signed_fill, fill_price, cur_pnl, generation + (1 if cur_qty == 0 else 0)

    same_side = (cur_qty > 0) == (signed_fill > 0)
    if same_side:
        # 同向加仓：加权平均
        new_qty = cur_qty + signed_fill
        new_avg = (cur_qty * cur_avg + signed_fill * fill_price) / new_qty
        return new_qty, new_avg, cur_pnl, generation

    # 反向：先 realize 平掉一部分
    closed_qty = min(abs(signed_fill), abs(cur_qty))
    # 多头平仓盈亏 = (fill_price - avg) * closed_qty；空头反过来
    sign_long = Decimal(1) if cur_qty > 0 else Decimal(-1)
    realized = (fill_price - cur_avg) * closed_qty * sign_long
    new_pnl = cur_pnl + realized

    new_qty = cur_qty + signed_fill  # 减仓后的剩余量（可能跨过 0）
    if new_qty == 0:
        # 完全平仓
        return Decimal(0), Decimal(0), new_pnl, generation
    if (new_qty > 0) == (cur_qty > 0):
        # 减仓未平：保持原 avg
        return new_qty, cur_avg, new_pnl, generation
    # 跨过 0 → 反向开新仓，generation++
    return new_qty, fill_price, new_pnl, generation + 1


async def list_by_account(
    conn: AsyncConnection,
    account_id: UUID,
    *,
    include_flat: bool = False,
) -> list[dict[str, Any]]:
    """列出某 account 的所有持仓。默认过滤掉 quantity=0 的（已平仓但保留行）。"""
    sql = (
        "SELECT venue, symbol, quantity, avg_open_price, realized_pnl, "
        "generation, updated_at "
        "FROM positions WHERE account_id = %s"
    )
    if not include_flat:
        sql += " AND quantity <> 0"
    sql += " ORDER BY updated_at DESC"

    async with conn.cursor() as cur:
        await cur.execute(sql, (str(account_id),))
        rows = await cur.fetchall()
    return list(rows)  # type: ignore[arg-type]
