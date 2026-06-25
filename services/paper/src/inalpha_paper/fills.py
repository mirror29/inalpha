"""一笔 fill 落账的共享逻辑：positions + cash + closed_trades（D-11 抽出）。

``POST /orders/submit``（api/orders.py）与 ``POST /plans/{id}/execute``
（api/trade_plans.py）两条路径都要把一笔成交写进 positions / cash / closed_trades，
原本各写一遍且 plan 路径漏传 ``ts_event`` / ``order_id``（apply_fill 必填）+ 漏写
closed_trades。统一到这里：

- cash delta 入该 instrument 的**计价货币桶**（``currency_resolver`` 解析）
- positions 记 currency 列
- 检测到平仓时同事务写 closed_trades（D-9.1a 链路）

**调用方必须包在事务里**（与 storage.positions.apply_fill 约定一致）。
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from .execution import perp_margin
from .execution.currency_resolver import resolve_currency
from .storage import accounts as accounts_store
from .storage import closed_trades as closed_trades_store
from .storage import positions as positions_store


async def _update_perp_margin(
    db: Any, account_id: UUID, venue: str, symbol: str,
    new_row: dict[str, Any], leverage: int,
) -> None:
    """按成交后持仓重写 perp 的 leverage / margin_used / liquidation_price(逐仓口径)。

    逐仓简化:用分配保证金 IM 作该仓 isolated wallet 估算强平价(v1 不模拟加保证金)。
    flat → margin_used=0 / liquidation_price=NULL。
    """
    new_qty = Decimal(str(new_row["quantity"]))
    new_avg = Decimal(str(new_row["avg_open_price"]))
    if new_qty == 0:
        margin_used: Decimal = Decimal(0)
        liq: Decimal | None = None
    else:
        margin_used = abs(new_qty) * new_avg / Decimal(leverage)
        side_i = 1 if new_qty > 0 else -1
        liq = Decimal(str(perp_margin.liquidation_price(
            side=side_i, qty_abs=float(abs(new_qty)),
            entry_price=float(new_avg), wallet_balance=float(margin_used),
        )))
    async with db.cursor() as cur:
        await cur.execute(
            "UPDATE positions SET leverage=%s, margin_used=%s, liquidation_price=%s "
            "WHERE account_id=%s AND venue=%s AND symbol=%s",
            (leverage, margin_used, liq, str(account_id), venue, symbol),
        )


async def apply_fill_to_positions_and_cash(
    db: Any,
    *,
    account_id: UUID,
    venue: str,
    symbol: str,
    side: str,
    quantity: Decimal,
    fill_price: Decimal,
    fee: Decimal,
    ts_event: datetime,
    order_id: str,
    trading_mode: str = "spot",
    leverage: int = 1,
) -> Decimal:
    """把一笔 fill 同时更新 positions + cash + closed_trades（在调用方的事务里）。

    返回**这笔成交的已实现盈亏**（毛口径，不减手续费，与 position.realized_pnl 同口径）：
    平/减仓单 = ``close_profit_abs``；纯开/加仓单 = ``0``。调用方据此回写 orders.realized_pnl。

    ``trading_mode``:
    - ``"spot"``（默认）:cash delta = ``∓notional − fee``（买减卖加,现货语义）。
    - ``"perp"``:**开/加仓不收付名义**,cash 只随已实现盈亏（平/减仓 ``close_profit_abs``）
      与 fee 变动;并按成交后持仓重写 ``leverage / margin_used / liquidation_price``
      （逐仓口径,与内存 ``Portfolio`` 同算法）。
    """
    currency = resolve_currency(venue, symbol)
    notional = quantity * fill_price

    # 先更新持仓(perp 现金口径依赖平仓信息 close_info;spot 不依赖,顺序无碍)
    new_row, close_info = await positions_store.apply_fill(
        db,
        account_id=account_id,
        venue=venue,
        symbol=symbol,
        side=side,
        fill_qty=quantity,
        fill_price=fill_price,
        ts_event=ts_event,
        order_id=order_id,
        currency=currency,
    )

    # 现金 delta:spot 动名义;perp 只动已实现盈亏 + fee
    if trading_mode == "perp":
        realized = (
            Decimal(str(close_info.close_profit_abs)) if close_info is not None else Decimal(0)
        )
        cash_delta = realized - fee
    else:
        cash_delta = (-notional if side == "BUY" else notional) - fee
    await accounts_store.apply_cash_delta(db, account_id, cash_delta, currency=currency)

    # perp:按成交后持仓重写保证金 / 强平价
    if trading_mode == "perp":
        await _update_perp_margin(db, account_id, venue, symbol, new_row, leverage)

    if close_info is not None:
        await closed_trades_store.insert_close(
            db,
            account_id=close_info.account_id,
            venue=close_info.venue,
            symbol=close_info.symbol,
            side=close_info.side,
            open_ts=close_info.open_ts,
            close_ts=close_info.close_ts,
            open_price=close_info.open_price,
            close_price=close_info.close_price,
            quantity=close_info.closed_qty,
            close_profit_pct=close_info.close_profit_pct,
            close_profit_abs=close_info.close_profit_abs,
            exit_reason=close_info.exit_reason,
            open_order_id=close_info.open_order_id,
            close_order_id=close_info.close_order_id,
        )
        return Decimal(str(close_info.close_profit_abs))
    return Decimal(0)
