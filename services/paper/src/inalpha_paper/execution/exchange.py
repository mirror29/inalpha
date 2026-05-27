"""``SimulatedExchange`` —— 回测专用 venue + Gateway。

行为约定（[refs/nautilus.md §6](../../../../docs/refs/nautilus.md)、
[refs/vnpy.md §6](../../../../docs/refs/vnpy.md) 综合）：

- **撮合保真度 L1**：用 Bar OHLC 撮合，不模拟 partial fill / queue position
- **市价单**：当前 bar 的 ``open`` 成交（标准回测惯例避免 lookahead）
- **限价单买**：``order.price >= bar.low`` 触发，成交价 ``min(order.price, bar.open)`` 保守
- **限价单卖**：``order.price <= bar.high`` 触发，成交价 ``max(order.price, bar.open)``
- **手续费**：固定比例 ``fee_rate * notional``，事后从 Portfolio 现金扣除
- **不模拟 slippage**：D-6+ 引入 FillModel 抽象时再加
- **spot 守门（D-9 修复）**：``bind_portfolio`` 注入后，撮合前用
  ``portfolio.can_afford_*`` 校验现金 / LONG 持仓，否则拒单（避免 cash 透支
  + 凭空 SHORT），ADR-0032 BuyingPowerRule 撮合层兜底实现

internal topic 约定（仅在本服务内使用）：

- ``internal.venue.accepted`` —— venue 接受订单（mark_accepted）
- ``internal.venue.filled`` —— venue 撮合成功
- ``internal.venue.rejected`` —— venue 拒单（如不支持的 OrderType / 现金不足）
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from ..kernel.clock import Clock
from ..kernel.identifiers import ClientOrderId, StrategyId, VenueOrderId
from ..kernel.msgbus import MessageBus
from ..model.data import Bar
from ..model.orders import Order, OrderSide, OrderType
from .gateway import Gateway

if TYPE_CHECKING:
    from ..engine.portfolio import Portfolio

EXECUTION_ENGINE_ENDPOINT = "ExecutionEngine.execute"


class SimulatedExchange(Gateway):
    """同时是 Gateway 和 venue 撮合器。"""

    def __init__(self, msgbus: MessageBus, clock: Clock) -> None:
        self._msgbus = msgbus
        self._clock = clock
        # 待撮合订单：list of (order, strategy_id)
        self._pending: list[tuple[Order, StrategyId]] = []
        self._next_id: int = 1
        # spot 守门用；BacktestEngine 构造完 Portfolio 后调 bind_portfolio 注入
        self._portfolio: Portfolio | None = None
        # 本轮 process_bar 内被 portfolio 守门拒的 client_order_id
        # 用于 process_bar 末把它们从 _pending 移除（避免下一根 bar 重复拒）
        self._denied_this_round: set[ClientOrderId] = set()

    def bind_portfolio(self, portfolio: Portfolio) -> None:
        """注入 Portfolio 让 ``_try_fill`` 能在撮合前做现金 / 持仓守门。

        BacktestEngine 必须构造完 Portfolio 后调一次；未调用时撮合层退化为
        旧行为（不守门，向后兼容老测试）。
        """
        self._portfolio = portfolio

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
        """对当前 bar 撮合 pending orders，返回成交笔数。

        ``_try_fill`` 在 portfolio 守门拒单时把 client_order_id 加进
        ``_denied_this_round``，循环末把它们从 _pending 一次性 drop（避免无穷
        重试同一笔守门必拒的订单 —— spot 现金不够 / 裸 SHORT）。
        """
        filled_count = 0
        remaining: list[tuple[Order, StrategyId]] = []
        self._denied_this_round.clear()

        for order, strategy_id in self._pending:
            if order.instrument_id != bar.instrument_id:
                remaining.append((order, strategy_id))
                continue

            fill = self._try_fill(order, bar, strategy_id)
            if fill is None:
                if order.client_order_id in self._denied_this_round:
                    # 被 portfolio 守门拒，不再留 pending
                    continue
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

    def _try_fill(
        self,
        order: Order,
        bar: Bar,
        strategy_id: StrategyId,
    ) -> tuple[float, float] | None:
        """返回 ``(fill_qty, fill_price)``；``None`` 表示**未触发**或**守门拒**。

        守门拒时把 ``client_order_id`` 加入 ``_denied_this_round``，让
        ``process_bar`` 把它从 pending drop。
        """
        fill_qty = order.quantity
        fill_price: float | None = None

        if order.type == OrderType.MARKET:
            # 市价单：用当前 bar 的 open 撮合（避免 lookahead）
            fill_price = bar.open
        elif order.type == OrderType.LIMIT:
            assert order.price is not None  # LIMIT 必带价
            if order.side == OrderSide.BUY:
                if order.price >= bar.low:
                    fill_price = min(order.price, bar.open)
            else:  # SELL
                if order.price <= bar.high:
                    fill_price = max(order.price, bar.open)

        if fill_price is None:
            return None  # LIMIT 未触发

        # spot 守门：portfolio 注入后启用；未注入时退化为旧行为
        if self._portfolio is not None:
            if order.side == OrderSide.BUY:
                if not self._portfolio.can_afford_buy(fill_qty, fill_price):
                    notional = fill_qty * fill_price
                    fee = notional * self._portfolio.fee_rate
                    self._emit_denied(
                        order,
                        strategy_id,
                        f"INSUFFICIENT_CASH: need {notional + fee:.4f}, "
                        f"have {self._portfolio.cash:.4f}",
                    )
                    return None
            else:  # SELL
                if not self._portfolio.can_afford_sell(order.instrument_id, fill_qty):
                    pos = self._portfolio.position(order.instrument_id)
                    current = pos.quantity if pos is not None else 0.0
                    self._emit_denied(
                        order,
                        strategy_id,
                        f"INSUFFICIENT_POSITION: need {fill_qty}, have {current} "
                        f"(spot 模式禁裸 SHORT)",
                    )
                    return None

        return (fill_qty, fill_price)

    def _emit_denied(self, order: Order, strategy_id: StrategyId, reason: str) -> None:
        """守门拒单：emit rejected + 加入 denied 集合让 process_bar drop。"""
        self._denied_this_round.add(order.client_order_id)
        self._msgbus.publish(
            "internal.venue.rejected",
            {
                "client_order_id": order.client_order_id,
                "strategy_id": strategy_id,
                "reason": reason,
                "ts": self._clock.now_ns(),
            },
        )
