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

from .execution.currency_resolver import resolve_currency
from .storage import accounts as accounts_store
from .storage import closed_trades as closed_trades_store
from .storage import positions as positions_store


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
) -> None:
    """把一笔 fill 同时更新 positions + cash + closed_trades（在调用方的事务里）。"""
    currency = resolve_currency(venue, symbol)
    notional = quantity * fill_price
    cash_delta = (-notional if side == "BUY" else notional) - fee
    await accounts_store.apply_cash_delta(db, account_id, cash_delta, currency=currency)
    _, close_info = await positions_store.apply_fill(
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
