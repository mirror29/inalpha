"""``_detect_close`` —— Portfolio fill 是否包含平仓部分（ADR-0007 §D2）。

纯函数 + 不可变 dataclass，零副作用。Portfolio.on_fill 在 ``apply_fill`` 之前调
（需要 prev_position 快照），把检测出的 ``ClosedTradeStaging`` 入队等 worker 写 DB。

覆盖 4 种 fill 后果（与 `model.positions.Position.apply_fill` 对齐）：

- prev FLAT + fill → 开仓 → 返 None
- prev 同向 + fill → 加仓 → 返 None
- prev 反向 + fill（减仓未平 / 完全平仓 / 跨过 0 反向开新仓）→ 返 ClosedTradeStaging（含平掉部分）

关于跨过 0 反向开仓：本函数**只**返回平掉的部分，剩下的反向开新仓不计入（新 trade 在
完全平掉之前不能写 closed_trades）。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from ..model.events import OrderFilled
from ..model.orders import OrderSide
from ..model.positions import Position


@dataclass(frozen=True, slots=True)
class ClosedTradeStaging:
    """Portfolio 检测到 close 后入队的暂存数据，待 ClosedTradesWriter 异步写 DB。

    字段与 `storage.closed_trades.insert_close` 参数对齐。
    """

    account_id: UUID
    venue: str
    symbol: str
    side: Literal["long", "short"]
    """**平仓前持仓方向**（不是 fill 的 BUY/SELL）。"""
    open_ts: datetime
    close_ts: datetime
    open_price: Decimal
    close_price: Decimal
    quantity: Decimal
    """实际平掉的量（≤ abs(prev_position.quantity)）。"""
    close_profit_pct: float
    close_profit_abs: float
    exit_reason: str
    """from Order.tag or 'signal' 默认。必须在 closed_trades.exit_reason CHECK 集合内。"""
    open_order_id: str | None
    close_order_id: str


def _ns_to_dt(ts_ns: int) -> datetime:
    """epoch ns → tz-aware UTC datetime。"""
    return datetime.fromtimestamp(ts_ns / 1_000_000_000, tz=UTC)


def detect_close(
    prev_position: Position,
    fill: OrderFilled,
    *,
    account_id: UUID,
    order_tag: str | None = None,
) -> ClosedTradeStaging | None:
    """检测 fill 是否包含平仓部分；返回 ClosedTradeStaging 或 None。

    Args:
        prev_position: **apply_fill 之前**的 Position 快照（必须，否则 P&L 错位）
        fill: OrderFilled event
        account_id: 写库时用的账户 ID（外部传入，本函数不知道）
        order_tag: 来自 Order.tag。None 默认 'signal'
    """
    if prev_position.is_flat:
        return None

    fill_is_buy = fill.side == OrderSide.BUY
    prev_is_long = prev_position.quantity > 0

    # 同方向加仓（long+BUY 或 short+SELL）→ 不是平仓
    if fill_is_buy == prev_is_long:
        return None

    # 反方向 → 平至少一部分
    closed_qty = min(abs(prev_position.quantity), fill.fill_quantity)

    if prev_is_long:
        pnl_abs = (fill.fill_price - prev_position.avg_open_price) * closed_qty
    else:
        pnl_abs = (prev_position.avg_open_price - fill.fill_price) * closed_qty

    pnl_pct = (
        pnl_abs / (prev_position.avg_open_price * closed_qty)
        if prev_position.avg_open_price > 0
        else 0.0
    )

    venue = ""
    symbol = ""
    if fill.instrument_id is not None:
        venue = fill.instrument_id.venue
        symbol = fill.instrument_id.symbol

    return ClosedTradeStaging(
        account_id=account_id,
        venue=venue,
        symbol=symbol,
        side="long" if prev_is_long else "short",
        open_ts=_ns_to_dt(prev_position.ts_opened),
        close_ts=_ns_to_dt(fill.ts_event),
        open_price=Decimal(str(prev_position.avg_open_price)),
        close_price=Decimal(str(fill.fill_price)),
        quantity=Decimal(str(closed_qty)),
        close_profit_pct=float(pnl_pct),
        close_profit_abs=float(pnl_abs),
        exit_reason=order_tag or "signal",
        open_order_id=prev_position.open_order_id,
        close_order_id=str(fill.client_order_id),
    )
