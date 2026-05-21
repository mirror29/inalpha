"""``Strategy`` —— ``Actor`` + 订单 / 持仓接口。

用户子类化 ``Strategy`` 实现交易策略。``submit_order`` 走 ``msgbus.send`` 推到
``RiskEngine.execute`` endpoint，**不直接调 Gateway**。
"""
from __future__ import annotations

from ..kernel.clock import Clock
from ..kernel.identifiers import ClientOrderId, StrategyId
from ..kernel.msgbus import MessageBus
from ..model.commands import CancelOrderCommand, ModifyOrderCommand, SubmitOrderCommand
from ..model.events import (
    OrderAccepted,
    OrderCanceled,
    OrderFilled,
    OrderRejected,
    OrderSubmitted,
    PositionChanged,
    PositionClosed,
    PositionOpened,
)
from ..model.orders import Order
from .actor import Actor

# 标准 endpoint 名 —— D-5 起 RiskEngine 必须注册这个 endpoint
RISK_ENGINE_ENDPOINT = "RiskEngine.execute"


class Strategy(Actor):
    """用户策略基类。

    子类化时实例化前两个参数 ``(name, clock, msgbus)`` 由 Engine 注入。
    用户覆盖 ``on_*`` 回调；在回调里调 ``submit_order`` / ``cancel_order``。
    """

    def __init__(self, name: str, clock: Clock, msgbus: MessageBus) -> None:
        super().__init__(name, clock, msgbus)
        self._strategy_id = StrategyId(name)

        # 订阅本策略的订单 / 仓位事件
        self._msgbus.subscribe(f"events.order.{name}", self._handle_order_event)
        self._msgbus.subscribe(f"events.position.{name}", self._handle_position_event)

    @property
    def strategy_id(self) -> StrategyId:
        return self._strategy_id

    # ─── 下单 / 撤单 / 改单（统一走 endpoint） ───

    def submit_order(self, order: Order) -> None:
        """提交订单。订单经 RiskEngine → ExecutionEngine → Gateway 链路。

        D-4 阶段 RiskEngine 尚未实现，调用前必须先注册 ``RISK_ENGINE_ENDPOINT``
        否则抛 ``KeyError``（不静默吞）。
        """
        cmd = SubmitOrderCommand(
            order=order,
            strategy_id=self._strategy_id,
            ts_init=self._clock.now_ns(),
        )
        self._msgbus.send(RISK_ENGINE_ENDPOINT, cmd)

    def cancel_order(self, client_order_id: ClientOrderId, reason: str | None = None) -> None:
        cmd = CancelOrderCommand(
            client_order_id=client_order_id,
            strategy_id=self._strategy_id,
            ts_init=self._clock.now_ns(),
            reason=reason,
        )
        self._msgbus.send(RISK_ENGINE_ENDPOINT, cmd)

    def modify_order(
        self,
        client_order_id: ClientOrderId,
        new_quantity: float | None = None,
        new_price: float | None = None,
    ) -> None:
        if new_quantity is None and new_price is None:
            raise ValueError("modify_order: must specify new_quantity or new_price")
        cmd = ModifyOrderCommand(
            client_order_id=client_order_id,
            strategy_id=self._strategy_id,
            ts_init=self._clock.now_ns(),
            new_quantity=new_quantity,
            new_price=new_price,
        )
        self._msgbus.send(RISK_ENGINE_ENDPOINT, cmd)

    # ─── 框架内部事件分发 ───

    def _handle_order_event(self, msg: object) -> None:
        if isinstance(msg, OrderSubmitted):
            self.on_order_submitted(msg)
        elif isinstance(msg, OrderAccepted):
            self.on_order_accepted(msg)
        elif isinstance(msg, OrderFilled):
            self.on_order_filled(msg)
        elif isinstance(msg, OrderRejected):
            self.on_order_rejected(msg)
        elif isinstance(msg, OrderCanceled):
            self.on_order_canceled(msg)

    def _handle_position_event(self, msg: object) -> None:
        if isinstance(msg, PositionOpened):
            self.on_position_opened(msg)
        elif isinstance(msg, PositionChanged):
            self.on_position_changed(msg)
        elif isinstance(msg, PositionClosed):
            self.on_position_closed(msg)

    # ─── 用户覆盖的事件回调 ───

    def on_order_submitted(self, event: OrderSubmitted) -> None: ...
    def on_order_accepted(self, event: OrderAccepted) -> None: ...
    def on_order_filled(self, event: OrderFilled) -> None: ...
    def on_order_rejected(self, event: OrderRejected) -> None: ...
    def on_order_canceled(self, event: OrderCanceled) -> None: ...
    def on_position_opened(self, event: PositionOpened) -> None: ...
    def on_position_changed(self, event: PositionChanged) -> None: ...
    def on_position_closed(self, event: PositionClosed) -> None: ...
