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

D-9.1a 增强：apply_fill 现在返回 ``ClosedTradeInfo | None``，让调用方在同事务内
写入 ``closed_trades`` 表。同时记录 ``ts_opened`` / ``open_order_id`` 供平仓时
构造完整的 closed_trade 行。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from psycopg import AsyncConnection


@dataclass(frozen=True, slots=True)
class ClosedTradeInfo:
    """apply_fill 检测到平仓时返回的信息，供调用方写入 closed_trades 表。

    ``closed_qty > 0`` 必填；其余字段仅在 closed_qty > 0 时有意义。
    """

    account_id: UUID
    venue: str
    symbol: str
    side: str
    open_ts: datetime
    close_ts: datetime
    open_price: Decimal
    close_price: Decimal
    closed_qty: Decimal
    close_profit_pct: float
    close_profit_abs: float
    exit_reason: str
    open_order_id: str | None
    close_order_id: str


async def apply_fill(
    conn: AsyncConnection,
    *,
    account_id: UUID,
    venue: str,
    symbol: str,
    side: str,
    fill_qty: Decimal,
    fill_price: Decimal,
    ts_event: datetime,
    order_id: str,
    currency: str | None = None,
) -> tuple[dict[str, Any], ClosedTradeInfo | None]:
    """一笔 fill 应用到 positions 表。返回 (更新后的持仓行, 平仓信息或 None)。

    流程：SELECT 当前行 → Python 算新值 → UPSERT 回去。**调用方必须包在事务里**。

    D-9.1a 增强：传入 ``ts_event`` / ``order_id`` 用于记录 ts_opened + open_order_id，
    并在检测到平仓时返回 ClosedTradeInfo。

    D-11 增强：``currency`` 记录该持仓的计价货币（``execution.currency_resolver``
    解析），供 ``/accounts/me`` 跨币种 equity 折算。``None`` 时不更新该列（向后兼容）。
    """
    # 1. 读当前持仓（不存在视为 flat）
    async with conn.cursor() as cur:
        await cur.execute(
            "SELECT quantity, avg_open_price, realized_pnl, generation, "
            "ts_opened, open_order_id "
            "FROM positions WHERE account_id = %s AND venue = %s AND symbol = %s "
            "FOR UPDATE",
            (str(account_id), venue, symbol),
        )
        row = await cur.fetchone()

    cur_qty = Decimal(row["quantity"]) if row else Decimal(0)  # type: ignore[index]
    cur_avg = Decimal(row["avg_open_price"]) if row else Decimal(0)  # type: ignore[index]
    cur_pnl = Decimal(row["realized_pnl"]) if row else Decimal(0)  # type: ignore[index]
    generation = int(row["generation"]) if row else 0  # type: ignore[index]
    cur_ts_opened: datetime | None = row["ts_opened"] if row else None  # type: ignore[index]
    cur_open_order_id: str | None = row["open_order_id"] if row else None  # type: ignore[index]

    # 2. 应用 fill
    signed_fill = fill_qty if side == "BUY" else -fill_qty
    new_qty, new_avg, new_pnl, new_gen, close_result = _reduce_position_with_close(
        cur_qty, cur_avg, cur_pnl, generation,
        signed_fill, fill_price,
    )

    # 3. 决定 ts_opened / open_order_id
    prev_was_flat = cur_qty == 0
    position_reversed = (cur_qty != 0 and new_qty != 0
                         and (new_qty > 0) != (cur_qty > 0))
    should_reset_open = prev_was_flat or position_reversed

    new_ts_opened = ts_event if should_reset_open else cur_ts_opened
    new_open_order_id = order_id if should_reset_open else cur_open_order_id
    if new_qty == 0:
        new_ts_opened = None
        new_open_order_id = None

    # 4. UPSERT（currency 用 COALESCE：传 None 时保留旧值，不覆盖成 NULL）
    async with conn.cursor() as cur:
        await cur.execute(
            """
            INSERT INTO positions (
                account_id, venue, symbol, quantity, avg_open_price,
                realized_pnl, generation, ts_opened, open_order_id, currency, updated_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
            ON CONFLICT (account_id, venue, symbol) DO UPDATE
              SET quantity       = EXCLUDED.quantity,
                  avg_open_price = EXCLUDED.avg_open_price,
                  realized_pnl   = EXCLUDED.realized_pnl,
                  generation     = EXCLUDED.generation,
                  ts_opened      = EXCLUDED.ts_opened,
                  open_order_id  = EXCLUDED.open_order_id,
                  currency       = COALESCE(EXCLUDED.currency, positions.currency),
                  updated_at     = NOW()
            RETURNING account_id, venue, symbol, quantity, avg_open_price,
                      realized_pnl, generation, ts_opened, open_order_id,
                      currency, updated_at
            """,
            (
                str(account_id), venue, symbol,
                new_qty, new_avg, new_pnl, new_gen,
                new_ts_opened, new_open_order_id, currency,
            ),
        )
        new_row = await cur.fetchone()

    # 5. 构造 ClosedTradeInfo
    close_info: ClosedTradeInfo | None = None
    if close_result is not None and close_result.closed_qty > 0:
        open_ts = cur_ts_opened if cur_ts_opened is not None else ts_event
        open_oid = cur_open_order_id
        close_info = ClosedTradeInfo(
            account_id=account_id,
            venue=venue,
            symbol=symbol,
            side=close_result.side,
            open_ts=open_ts,
            close_ts=ts_event,
            open_price=cur_avg,
            close_price=fill_price,
            closed_qty=close_result.closed_qty,
            close_profit_pct=close_result.close_profit_pct,
            close_profit_abs=close_result.close_profit_abs,
            exit_reason="signal",
            open_order_id=open_oid,
            close_order_id=order_id,
        )

    return new_row, close_info  # type: ignore[return-value]


@dataclass(frozen=True, slots=True)
class _CloseResult:
    """_reduce_position_with_close 的平仓检测结果。"""
    closed_qty: Decimal
    side: str
    close_profit_pct: float
    close_profit_abs: float


def _reduce_position_with_close(
    cur_qty: Decimal,
    cur_avg: Decimal,
    cur_pnl: Decimal,
    generation: int,
    signed_fill: Decimal,
    fill_price: Decimal,
) -> tuple[Decimal, Decimal, Decimal, int, _CloseResult | None]:
    """同 _reduce_position，但额外返回平仓检测结果。

    Returns:
        (new_qty, new_avg, new_pnl, new_generation, close_result_or_none)
    """
    if cur_qty == 0:
        return signed_fill, fill_price, cur_pnl, generation + 1, None

    same_side = (cur_qty > 0) == (signed_fill > 0)
    if same_side:
        new_qty = cur_qty + signed_fill
        new_avg = (cur_qty * cur_avg + signed_fill * fill_price) / new_qty
        return new_qty, new_avg, cur_pnl, generation, None

    # 反向：先 realize 平掉一部分
    closed_qty = min(abs(signed_fill), abs(cur_qty))
    sign_long = Decimal(1) if cur_qty > 0 else Decimal(-1)
    realized = (fill_price - cur_avg) * closed_qty * sign_long
    new_pnl = cur_pnl + realized

    # 平仓盈亏百分比
    close_profit_abs = float(realized)
    close_profit_pct = (
        close_profit_abs / float(cur_avg * closed_qty)
        if cur_avg > 0
        else 0.0
    )
    close_side = "long" if cur_qty > 0 else "short"

    new_qty = cur_qty + signed_fill
    if new_qty == 0:
        return Decimal(0), Decimal(0), new_pnl, generation, _CloseResult(
            closed_qty=closed_qty,
            side=close_side,
            close_profit_pct=close_profit_pct,
            close_profit_abs=close_profit_abs,
        )
    if (new_qty > 0) == (cur_qty > 0):
        return new_qty, cur_avg, new_pnl, generation, _CloseResult(
            closed_qty=closed_qty,
            side=close_side,
            close_profit_pct=close_profit_pct,
            close_profit_abs=close_profit_abs,
        )
    # 跨过 0 → 反向开新仓
    return new_qty, fill_price, new_pnl, generation + 1, _CloseResult(
        closed_qty=closed_qty,
        side=close_side,
        close_profit_pct=close_profit_pct,
        close_profit_abs=close_profit_abs,
    )


def _reduce_position(
    cur_qty: Decimal,
    cur_avg: Decimal,
    cur_pnl: Decimal,
    generation: int,
    signed_fill: Decimal,
    fill_price: Decimal,
) -> tuple[Decimal, Decimal, Decimal, int]:
    """纯函数：返回 (new_qty, new_avg, new_pnl, new_generation)。

    保留用于向后兼容——新代码请用 _reduce_position_with_close。
    """
    new_qty, new_avg, new_pnl, new_gen, _ = _reduce_position_with_close(
        cur_qty, cur_avg, cur_pnl, generation, signed_fill, fill_price,
    )
    return new_qty, new_avg, new_pnl, new_gen


async def get(
    conn: AsyncConnection,
    *,
    account_id: UUID,
    venue: str,
    symbol: str,
    for_update: bool = False,
) -> dict[str, Any] | None:
    """读单个 (account, venue, symbol) 持仓行；不存在返 None（live PnL / resume 重建用）。

    含 perp 列 ``leverage / margin_used / liquidation_price``(spot 为默认 1/0/NULL)。

    ``for_update=True``：``SELECT ... FOR UPDATE`` 锁住该行——spot long-only 守门的
    "读-检-写"必须在同一事务里 FOR UPDATE，否则并发 SELL 各自读到旧持仓双双过闸、
    apply_fill 把持仓打成负仓（TOCTOU）。**仅在事务内使用**（行锁随事务释放）。
    """
    sql = (
        "SELECT venue, symbol, quantity, avg_open_price, realized_pnl, "
        "generation, ts_opened, open_order_id, currency, updated_at, "
        "leverage, margin_used, liquidation_price "
        "FROM positions WHERE account_id = %s AND venue = %s AND symbol = %s"
    )
    if for_update:
        sql += " FOR UPDATE"
    async with conn.cursor() as cur:
        await cur.execute(sql, (str(account_id), venue, symbol))
        row = await cur.fetchone()
    return row  # type: ignore[return-value]


async def list_by_account(
    conn: AsyncConnection,
    account_id: UUID,
    *,
    include_flat: bool = False,
) -> list[dict[str, Any]]:
    """列出某 account 的所有持仓。默认过滤掉 quantity=0 的（已平仓但保留行）。"""
    sql = (
        "SELECT venue, symbol, quantity, avg_open_price, realized_pnl, "
        "generation, ts_opened, open_order_id, currency, updated_at "
        "FROM positions WHERE account_id = %s"
    )
    if not include_flat:
        sql += " AND quantity <> 0"
    sql += " ORDER BY updated_at DESC"

    async with conn.cursor() as cur:
        await cur.execute(sql, (str(account_id),))
        rows = await cur.fetchall()
    return list(rows)  # type: ignore[arg-type]
