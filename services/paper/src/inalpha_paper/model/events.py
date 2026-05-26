"""订单 / 持仓事件 —— 全部不可变 dataclass。

事件流向：

```
Strategy.submit_order
  → SubmitOrderCommand (commands.py)
  → RiskEngine.execute endpoint
  → ExecutionEngine.execute endpoint
  → Gateway.send_order
  → 交易所回报
  → Gateway.on_order_event
  → MessageBus topic events.order.<strategy>
  → Strategy.on_order_filled / on_order_canceled / ...
```

D-4 阶段尚未实现 Engine / Gateway，但事件类型先定义好，下游 D-5 直接用。
"""
from __future__ import annotations

from dataclasses import dataclass

from ..kernel.identifiers import ClientOrderId, InstrumentId, StrategyId, VenueOrderId
from .orders import OrderSide

# ─── 订单事件 ───


@dataclass(frozen=True, slots=True)
class OrderEvent:
    """订单事件基类。子类用 isinstance 分发。"""

    client_order_id: ClientOrderId
    strategy_id: StrategyId
    ts_event: int  # 事件发生（venue 时间戳）
    ts_init: int  # 系统接到


@dataclass(frozen=True, slots=True)
class OrderSubmitted(OrderEvent):
    """订单已发到 venue（还没确认）。"""


@dataclass(frozen=True, slots=True)
class OrderAccepted(OrderEvent):
    venue_order_id: VenueOrderId = VenueOrderId("")  # noqa: RUF009  NewType 调用无副作用


@dataclass(frozen=True, slots=True)
class OrderRejected(OrderEvent):
    reason: str = ""


@dataclass(frozen=True, slots=True)
class OrderFilled(OrderEvent):
    venue_order_id: VenueOrderId = VenueOrderId("")  # noqa: RUF009  同上
    instrument_id: InstrumentId | None = None
    side: OrderSide = OrderSide.BUY
    fill_quantity: float = 0.0
    fill_price: float = 0.0
    trade_id: str = ""
    is_last_fill: bool = False
    """成交分批：True 时本订单已 FILLED；False 时仍为 PARTIALLY_FILLED。"""
    tag: str | None = None
    """ADR-0007：从 Order.tag 透传，Portfolio 写 closed_trades.exit_reason 用。"""


@dataclass(frozen=True, slots=True)
class OrderCanceled(OrderEvent):
    reason: str | None = None


# ─── 仓位事件 ───


@dataclass(frozen=True, slots=True)
class PositionEvent:
    instrument_id: InstrumentId
    strategy_id: StrategyId
    quantity: float
    avg_open_price: float
    realized_pnl: float
    generation: int
    ts_event: int
    ts_init: int


@dataclass(frozen=True, slots=True)
class PositionOpened(PositionEvent):
    """首次从 FLAT 开仓。"""


@dataclass(frozen=True, slots=True)
class PositionChanged(PositionEvent):
    """已有仓位的方向 / 数量变化（加仓 / 减仓 / 反向）。"""


@dataclass(frozen=True, slots=True)
class PositionClosed(PositionEvent):
    """回到 FLAT。``realized_pnl`` 是这次平仓后的累计盈亏。"""
