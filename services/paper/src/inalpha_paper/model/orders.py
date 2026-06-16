"""``Order`` 与订单状态机。

状态机 7 个状态，比 Nautilus 14 个裁剪到 MVP 必要的（参考 [refs/nautilus.md §6](../../../../docs/refs/nautilus.md)）：

```
NEW → SUBMITTED → ACCEPTED → PARTIALLY_FILLED → FILLED
                                              ↘ CANCELED
                                                REJECTED
```

``Order`` 是**可变的**：方法 ``mark_*`` 推进状态（订单事件触发）。但**所有事件本身**
（``OrderSubmitted`` / ``OrderFilled`` ...）在 ``events.py`` 里是不可变 dataclass。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from ..kernel.identifiers import ClientOrderId, InstrumentId, VenueOrderId


class OrderSide(Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP_MARKET = "STOP_MARKET"
    STOP_LIMIT = "STOP_LIMIT"


class OrderStatus(Enum):
    """7 状态机（MVP 起步规模）。"""

    NEW = "NEW"
    SUBMITTED = "SUBMITTED"
    ACCEPTED = "ACCEPTED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELED = "CANCELED"
    REJECTED = "REJECTED"


#: 框架级保护性出场的 ``Order.tag`` 集合（ADR-0052）。放在 model 中立层：engine
#: （PositionGuard）与 execution（SimulatedExchange）都引它，避免 engine↔execution
#: 循环依赖（与下方 ``Order.tag`` 约定值同源）。
PROTECTIVE_EXIT_TAGS: frozenset[str] = frozenset(
    {"stop_loss", "take_profit", "trailing_stop_loss"}
)

#: ``PositionGuard`` 出场单 ``client_order_id`` 的专属前缀。与 ``PROTECTIVE_EXIT_TAGS``
#: 一起构成「不可仅靠 tag 仿冒」的双因子判定（见 ``is_protective_order``）。
GUARD_ORDER_PREFIX = "guard-"


# 合法转移表，违反抛 ``ValueError``
_VALID_TRANSITIONS: dict[OrderStatus, set[OrderStatus]] = {
    OrderStatus.NEW: {OrderStatus.SUBMITTED, OrderStatus.REJECTED},
    OrderStatus.SUBMITTED: {OrderStatus.ACCEPTED, OrderStatus.REJECTED, OrderStatus.CANCELED},
    OrderStatus.ACCEPTED: {
        OrderStatus.PARTIALLY_FILLED,
        OrderStatus.FILLED,
        OrderStatus.CANCELED,
        # D-9 ADR-0032：venue 接受订单后在撮合阶段被守门（cash / position）拒。
        # 业界惯例（broker 后置 risk gate / margin check）也允许这条路径。
        OrderStatus.REJECTED,
    },
    OrderStatus.PARTIALLY_FILLED: {OrderStatus.FILLED, OrderStatus.CANCELED},
    # 终态
    OrderStatus.FILLED: set(),
    OrderStatus.CANCELED: set(),
    OrderStatus.REJECTED: set(),
}


@dataclass(slots=True)
class Order:
    """订单 —— 可变状态机。

    创建：
        Order(client_order_id=..., instrument_id=..., side=..., type=..., quantity=...)

    状态推进通过 ``mark_*`` 方法，违反 7 状态机抛 ``ValueError``。
    """

    client_order_id: ClientOrderId
    instrument_id: InstrumentId
    side: OrderSide
    type: OrderType
    quantity: float
    price: float | None = None
    venue_order_id: VenueOrderId | None = None
    status: OrderStatus = OrderStatus.NEW
    filled_quantity: float = 0.0
    avg_fill_price: float | None = None
    ts_init: int = 0
    ts_last_event: int = 0
    # 拒绝 / 取消的原因（如果有）
    reason: str | None = None

    # ADR-0007：Strategy 显式标记，Portfolio 写入 closed_trades.exit_reason 时透传
    tag: str | None = None
    """半结构化语义标签。约定值（与 closed_trades.exit_reason CHECK 集合对齐）：
    'stop_loss' / 'trailing_stop_loss' / 'liquidation' / 'take_profit' / 'manual' / 'signal'。
    None 时 Portfolio 写 closed_trades 默认 'signal'。"""

    # 维护成交序列以便 reconcile（ADR-0017 live worker reconcile_state 用）
    _fills: list[tuple[float, float, int]] = field(default_factory=list)
    """list of (fill_quantity, fill_price, ts_event)"""

    def __post_init__(self) -> None:
        if self.quantity <= 0:
            raise ValueError(f"quantity must be positive, got {self.quantity}")
        if self.type in (OrderType.LIMIT, OrderType.STOP_LIMIT) and self.price is None:
            raise ValueError(f"{self.type.value} order requires price")
        if self.type == OrderType.MARKET and self.price is not None:
            raise ValueError("MARKET order must not specify price")

    # ─── 状态查询 ───

    @property
    def is_active(self) -> bool:
        """是否在活跃状态（可能成交、可能撤单）。"""
        return self.status in (
            OrderStatus.NEW,
            OrderStatus.SUBMITTED,
            OrderStatus.ACCEPTED,
            OrderStatus.PARTIALLY_FILLED,
        )

    @property
    def is_terminal(self) -> bool:
        return self.status in (OrderStatus.FILLED, OrderStatus.CANCELED, OrderStatus.REJECTED)

    @property
    def remaining_quantity(self) -> float:
        return self.quantity - self.filled_quantity

    # ─── 状态转移 ───

    def _transition(self, to: OrderStatus, ts: int) -> None:
        if to not in _VALID_TRANSITIONS[self.status]:
            raise ValueError(f"invalid transition: {self.status.value} → {to.value}")
        self.status = to
        self.ts_last_event = ts

    def mark_submitted(self, ts: int) -> None:
        self._transition(OrderStatus.SUBMITTED, ts)

    def mark_accepted(self, venue_order_id: VenueOrderId, ts: int) -> None:
        self.venue_order_id = venue_order_id
        self._transition(OrderStatus.ACCEPTED, ts)

    def mark_rejected(self, reason: str, ts: int) -> None:
        self.reason = reason
        self._transition(OrderStatus.REJECTED, ts)

    def apply_fill(self, fill_quantity: float, fill_price: float, ts: int) -> None:
        """累计一次成交。最后一次成交触发 ``PARTIALLY_FILLED → FILLED``（如果填完）。"""
        if self.status not in (OrderStatus.ACCEPTED, OrderStatus.PARTIALLY_FILLED):
            raise ValueError(f"cannot fill order in status {self.status.value}")
        if fill_quantity <= 0:
            raise ValueError(f"fill_quantity must be positive, got {fill_quantity}")
        if fill_quantity > self.remaining_quantity + 1e-9:  # 浮点容差
            raise ValueError(
                f"fill_quantity {fill_quantity} exceeds remaining {self.remaining_quantity}"
            )

        # 用加权平均更新 avg_fill_price
        prev_total = self.filled_quantity * (self.avg_fill_price or 0.0)
        new_total = prev_total + fill_quantity * fill_price
        self.filled_quantity += fill_quantity
        self.avg_fill_price = new_total / self.filled_quantity

        self._fills.append((fill_quantity, fill_price, ts))

        # 推进状态
        if abs(self.filled_quantity - self.quantity) < 1e-9:
            # 填满了
            if self.status in (OrderStatus.ACCEPTED, OrderStatus.PARTIALLY_FILLED):
                self._transition(OrderStatus.FILLED, ts)
        else:
            # 部分成交
            if self.status == OrderStatus.ACCEPTED:
                self._transition(OrderStatus.PARTIALLY_FILLED, ts)
            else:
                # 已经是 PARTIALLY_FILLED，只更新 ts
                self.ts_last_event = ts

    def mark_canceled(self, ts: int, reason: str | None = None) -> None:
        if reason:
            self.reason = reason
        self._transition(OrderStatus.CANCELED, ts)


def is_protective_order(order: Order) -> bool:
    """是否为 ``PositionGuard`` 框架兜底出场单（享风控豁免：跳过开仓闸 + notional 上限）。

    **双因子判定**（ADR-0052 / CR #88 major）：``tag`` ∈ ``PROTECTIVE_EXIT_TAGS`` **且**
    ``client_order_id`` 以 ``GUARD_ORDER_PREFIX`` 开头。单看 tag 不够——策略代码能自由设
    ``Order(tag="stop_loss")`` 仿冒以绕过风控；guard 出场单的 client_order_id 由框架按
    ``GUARD_ORDER_PREFIX`` 生成，二者同时满足才认定为框架兜底单。
    """
    return order.tag in PROTECTIVE_EXIT_TAGS and str(
        order.client_order_id
    ).startswith(GUARD_ORDER_PREFIX)
