"""``SimulatedExchange`` —— 回测专用 venue + Gateway。

行为约定（[refs/nautilus.md §6](../../../../docs/refs/nautilus.md)、
[refs/vnpy.md §6](../../../../docs/refs/vnpy.md) 综合）：

- **撮合保真度 L1**：用 Bar OHLC 撮合，不模拟 partial fill / queue position
- **市价单**：当前 bar 的 ``open`` 成交（标准回测惯例避免 lookahead）
- **限价单买**：``order.price >= bar.low`` 触发，成交价 ``min(order.price, bar.open)`` 保守
- **限价单卖**：``order.price <= bar.high`` 触发，成交价 ``max(order.price, bar.open)``
- **手续费**：固定比例 ``fee_rate * notional``，事后从 Portfolio 现金扣除
- **不模拟 slippage**：D-6+ 引入 FillModel 抽象时再加

internal topic 约定（仅在本服务内使用）：

- ``internal.venue.accepted`` —— venue 接受订单（mark_accepted）
- ``internal.venue.filled`` —— venue 撮合成功
- ``internal.venue.rejected`` —— venue 拒单（如不支持的 OrderType）
"""
from __future__ import annotations

from ..kernel.clock import Clock
from ..kernel.identifiers import ClientOrderId, StrategyId, VenueOrderId
from ..kernel.msgbus import MessageBus
from ..model.data import Bar
from ..model.orders import Order, OrderSide, OrderType
from .gateway import Gateway

EXECUTION_ENGINE_ENDPOINT = "ExecutionEngine.execute"


class SimulatedExchange(Gateway):
    """同时是 Gateway 和 venue 撮合器。"""

    def __init__(self, msgbus: MessageBus, clock: Clock) -> None:
        self._msgbus = msgbus
        self._clock = clock
        # 待撮合订单：list of (order, strategy_id)
        self._pending: list[tuple[Order, StrategyId]] = []
        self._next_id: int = 1

    # ─── Gateway interface ───

    def send_order(self, order: Order, strategy_id: StrategyId) -> None:
        """立即 accept（venue 撮合不需要排队等 ack），加入 pending。"""
        if order.type not in (OrderType.MARKET, OrderType.LIMIT):
            self._msgbus.publish(
                "internal.venue.rejected",
                {
                    "client_order_id": order.client_order_id,
                    "strategy_id": strategy_id,
                    "reason": f"OrderType {order.type.value} not supported by SimulatedExchange",
                    "ts": self._clock.now_ns(),
                },
            )
            return

        venue_id = VenueOrderId(f"sim-{self._next_id}")
        self._next_id += 1
        self._msgbus.publish(
            "internal.venue.accepted",
            {
                "client_order_id": order.client_order_id,
                "venue_order_id": venue_id,
                "strategy_id": strategy_id,
                "ts": self._clock.now_ns(),
            },
        )
        self._pending.append((order, strategy_id))

    def cancel_order(self, client_order_id: ClientOrderId) -> None:
        """从 pending 移除。撮合后才到撤单视为"撤单失败"（noop）。"""
        for i, (order, strategy_id) in enumerate(self._pending):
            if order.client_order_id == client_order_id:
                del self._pending[i]
                self._msgbus.publish(
                    "internal.venue.canceled",
                    {
                        "client_order_id": client_order_id,
                        "strategy_id": strategy_id,
                        "ts": self._clock.now_ns(),
                    },
                )
                return

    # ─── 撮合（BacktestEngine 主循环每根 bar 调一次） ───

    def process_bar(self, bar: Bar) -> int:
        """对当前 bar 撮合 pending orders，返回成交笔数。"""
        filled_count = 0
        remaining: list[tuple[Order, StrategyId]] = []

        for order, strategy_id in self._pending:
            if order.instrument_id != bar.instrument_id:
                remaining.append((order, strategy_id))
                continue

            fill = self._try_fill(order, bar)
            if fill is None:
                remaining.append((order, strategy_id))
                continue

            fill_qty, fill_price = fill
            trade_id = f"trade-{self._next_id}"
            self._next_id += 1

            self._msgbus.publish(
                "internal.venue.filled",
                {
                    "client_order_id": order.client_order_id,
                    "strategy_id": strategy_id,
                    "instrument_id": order.instrument_id,
                    "side": order.side,
                    "fill_qty": fill_qty,
                    "fill_price": fill_price,
                    "trade_id": trade_id,
                    "ts": bar.ts_event,
                },
            )
            filled_count += 1

        self._pending = remaining
        return filled_count

    def pending_count(self) -> int:
        return len(self._pending)

    # ─── 内部：撮合规则 ───

    def _try_fill(self, order: Order, bar: Bar) -> tuple[float, float] | None:
        """返回 ``(fill_qty, fill_price)`` 或 ``None``（未触发）。"""
        if order.type == OrderType.MARKET:
            # 市价单：用当前 bar 的 open 撮合（避免 lookahead）
            return (order.quantity, bar.open)

        if order.type == OrderType.LIMIT:
            assert order.price is not None  # LIMIT 必带价
            if order.side == OrderSide.BUY:
                # 价位触及 bar 区间 → 成交于 min(限价, open) 保守
                if order.price >= bar.low:
                    return (order.quantity, min(order.price, bar.open))
            else:  # SELL
                if order.price <= bar.high:
                    return (order.quantity, max(order.price, bar.open))

        return None
