"""``ExecutionEngine`` —— Order 状态机管理 + 路由到 Gateway。

职责（[refs/nautilus.md §5 §6](../../../../docs/refs/nautilus.md)）：

1. 注册 ``EXECUTION_ENGINE_ENDPOINT``，处理 SubmitOrder / Cancel / Modify
2. 维护 ``ClientOrderId -> Order`` 字典（所有正在生命周期内的 Order）
3. 接收 venue 内部事件（accepted / filled / rejected / canceled），更新 Order 状态机
4. 把状态变化翻译成对外事件（OrderSubmitted / Accepted / Filled / ...）发到
   ``events.order.<strategy_id>``，给 Strategy 消费
5. 把成交转发到 ``events.fills.<instrument_id>`` 给 Portfolio 消费
"""
from __future__ import annotations

from typing import Any, cast

from ..kernel.identifiers import ClientOrderId, InstrumentId, StrategyId, VenueOrderId
from ..kernel.msgbus import MessageBus
from ..model.commands import CancelOrderCommand, SubmitOrderCommand
from ..model.events import (
    OrderAccepted,
    OrderCanceled,
    OrderFilled,
    OrderRejected,
    OrderSubmitted,
)
from ..model.orders import Order, OrderSide, OrderStatus
from .exchange import EXECUTION_ENGINE_ENDPOINT
from .gateway import Gateway


class ExecutionEngine:
    """Order 生命周期管理。"""

    def __init__(self, msgbus: MessageBus, gateway: Gateway) -> None:
        self._msgbus = msgbus
        self._gateway = gateway
        self._orders: dict[ClientOrderId, Order] = {}
        # ClientOrderId -> StrategyId（venue 回报不带 strategy_id，缓存一下）
        self._strategy_index: dict[ClientOrderId, StrategyId] = {}

        msgbus.register_endpoint(EXECUTION_ENGINE_ENDPOINT, self._handle_command)
        msgbus.subscribe("internal.venue.accepted", self._handle_accepted)
        msgbus.subscribe("internal.venue.filled", self._handle_filled)
        msgbus.subscribe("internal.venue.rejected", self._handle_rejected)
        msgbus.subscribe("internal.venue.canceled", self._handle_canceled)

    # ─── 命令处理 ───

    def _handle_command(self, msg: object) -> None:
        if isinstance(msg, SubmitOrderCommand):
            self._submit(msg)
        elif isinstance(msg, CancelOrderCommand):
            self._cancel(msg)

    def _submit(self, cmd: SubmitOrderCommand) -> None:
        order = cmd.order
        order.mark_submitted(cmd.ts_init)
        self._orders[order.client_order_id] = order
        self._strategy_index[order.client_order_id] = cmd.strategy_id

        # 给 strategy 发 OrderSubmitted
        self._publish_order_event(
            cmd.strategy_id,
            OrderSubmitted(
                client_order_id=order.client_order_id,
                strategy_id=cmd.strategy_id,
                ts_event=cmd.ts_init,
                ts_init=cmd.ts_init,
            ),
        )

        # 推到 gateway
        self._gateway.send_order(order, cmd.strategy_id)

    def _cancel(self, cmd: CancelOrderCommand) -> None:
        if cmd.client_order_id not in self._orders:
            return  # 静默丢，已是终态
        self._gateway.cancel_order(cmd.client_order_id)

    # ─── venue 事件 ───

    def _handle_accepted(self, msg: object) -> None:
        d = cast(dict[str, Any], msg)
        order = self._orders.get(d["client_order_id"])
        if order is None:
            return
        venue_id = cast(VenueOrderId, d["venue_order_id"])
        order.mark_accepted(venue_id, d["ts"])

        self._publish_order_event(
            d["strategy_id"],
            OrderAccepted(
                client_order_id=order.client_order_id,
                strategy_id=d["strategy_id"],
                ts_event=d["ts"],
                ts_init=d["ts"],
                venue_order_id=venue_id,
            ),
        )

    def _handle_filled(self, msg: object) -> None:
        d = cast(dict[str, Any], msg)
        client_id = cast(ClientOrderId, d["client_order_id"])
        order = self._orders.get(client_id)
        if order is None:
            return

        fill_qty = float(d["fill_qty"])
        fill_price = float(d["fill_price"])
        ts = int(d["ts"])
        order.apply_fill(fill_qty, fill_price, ts)

        is_last = order.status == OrderStatus.FILLED
        evt = OrderFilled(
            client_order_id=order.client_order_id,
            strategy_id=d["strategy_id"],
            ts_event=ts,
            ts_init=ts,
            venue_order_id=order.venue_order_id or VenueOrderId(""),
            instrument_id=cast(InstrumentId, d["instrument_id"]),
            side=cast(OrderSide, d["side"]),
            fill_quantity=fill_qty,
            fill_price=fill_price,
            trade_id=str(d.get("trade_id", "")),
            tag=order.tag,  # ADR-0007 透传 Order.tag
            is_last_fill=is_last,
        )
        # 给 strategy
        self._publish_order_event(d["strategy_id"], evt)
        # 给 Portfolio（订阅 events.fills.<instrument_id>）
        self._msgbus.publish(f"events.fills.{order.instrument_id}", evt)

        if order.is_terminal:
            self._cleanup(client_id)

    def _handle_rejected(self, msg: object) -> None:
        d = cast(dict[str, Any], msg)
        client_id = cast(ClientOrderId, d["client_order_id"])
        order = self._orders.get(client_id)
        if order is None:
            return

        reason = str(d.get("reason", "rejected by venue"))
        ts = int(d["ts"])
        order.mark_rejected(reason, ts)

        self._publish_order_event(
            d["strategy_id"],
            OrderRejected(
                client_order_id=order.client_order_id,
                strategy_id=d["strategy_id"],
                ts_event=ts,
                ts_init=ts,
                reason=reason,
            ),
        )
        self._cleanup(client_id)

    def _handle_canceled(self, msg: object) -> None:
        d = cast(dict[str, Any], msg)
        client_id = cast(ClientOrderId, d["client_order_id"])
        order = self._orders.get(client_id)
        if order is None:
            return

        ts = int(d["ts"])
        order.mark_canceled(ts)
        self._publish_order_event(
            d["strategy_id"],
            OrderCanceled(
                client_order_id=order.client_order_id,
                strategy_id=d["strategy_id"],
                ts_event=ts,
                ts_init=ts,
            ),
        )
        self._cleanup(client_id)

    # ─── 工具 ───

    def _publish_order_event(self, strategy_id: StrategyId, event: object) -> None:
        self._msgbus.publish(f"events.order.{strategy_id}", event)

    def _cleanup(self, client_id: ClientOrderId) -> None:
        """订单进入终态，从内存清理（保留事件流即可重建）。"""
        self._orders.pop(client_id, None)
        self._strategy_index.pop(client_id, None)

    # ─── inspection（测试用） ───

    def get_order(self, client_id: ClientOrderId) -> Order | None:
        return self._orders.get(client_id)

    def active_count(self) -> int:
        return len(self._orders)
