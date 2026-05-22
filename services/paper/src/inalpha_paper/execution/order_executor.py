"""``OrderExecutor`` —— D-8a 单笔订单执行器（in-memory，stateless）。

设计动机：

``BacktestEngine`` 是 long-running 主循环（一根一根 bar 喂进来），不适合"来一单
执行一单"的 ``POST /orders/submit`` 场景。为了不为单笔下单起完整 msgbus + portfolio
栈，把 SimulatedExchange 的撮合规则提炼成一个纯函数，stateless 即可：

- MARKET：直接成交于 ``ref_price``
- LIMIT BUY：``order.price >= ref_price`` 时触发，成交价 ``min(order.price, ref_price)`` 保守
- LIMIT SELL：``order.price <= ref_price`` 时触发，成交价 ``max(order.price, ref_price)`` 保守

D-8a 范围：

- 不维持持仓 / 现金 / 累计 PnL（plan/exec 端到端跑通先，状态留 D-8b 持久化时一起做）
- ``ref_price`` 由调用方传入（orchestration 层负责拉最新价 / 或用户显式指定）
- 单进程 in-memory：``client_order_id`` 自增、不重启持久

后续 D-8b 接持久化时，本模块会拆成 ``OrderService``，把成交事件流写进 DB +
Portfolio。当前文件**只关心一次成交结算**。
"""
from __future__ import annotations

import itertools
from datetime import UTC, datetime
from threading import Lock
from typing import Literal

_counter = itertools.count(1)
_lock = Lock()


def _next_client_order_id(prefix: str = "ord") -> str:
    """进程内单调自增的 ``client_order_id``。线程安全（FastAPI sync route 也安全）。"""
    with _lock:
        n = next(_counter)
    return f"{prefix}-{n:08d}"


class OrderExecutor:
    """单笔订单 stateless 执行器。"""

    @staticmethod
    def execute(
        *,
        venue: str,
        symbol: str,
        side: Literal["BUY", "SELL"],
        order_type: Literal["MARKET", "LIMIT"],
        quantity: float,
        price: float | None,
        ref_price: float,
        fee_rate: float,
    ) -> dict[str, object]:
        """同步撮合一笔订单，返回 dict（直接喂给 ``SubmitOrderResponse``）。

        撮合规则见模块 docstring。

        Returns
        -------
        dict with keys::

            client_order_id, venue, symbol, side, order_type,
            requested_quantity, requested_price,
            status ("FILLED" | "REJECTED"),
            filled_quantity, avg_fill_price, fee, notional,
            rejection_reason, ts_event
        """
        client_order_id = _next_client_order_id()
        ts_event = datetime.now(UTC)

        base = {
            "client_order_id": client_order_id,
            "venue": venue,
            "symbol": symbol,
            "side": side,
            "order_type": order_type,
            "requested_quantity": quantity,
            "requested_price": price,
            "ts_event": ts_event,
        }

        fill = OrderExecutor._compute_fill(
            side=side,
            order_type=order_type,
            price=price,
            ref_price=ref_price,
        )

        if fill is None:
            # LIMIT 未触发 → 在 D-8a 在线撮合语义里视为"立即拒绝"
            # （区别于回测里"挂着等下一根 bar"——撮合是同步语义）
            return {
                **base,
                "status": "REJECTED",
                "filled_quantity": 0.0,
                "avg_fill_price": None,
                "fee": 0.0,
                "notional": 0.0,
                "rejection_reason": (
                    f"LIMIT {side} price {price} not triggered against ref {ref_price}"
                ),
            }

        fill_price = fill
        notional = quantity * fill_price
        fee = notional * fee_rate

        return {
            **base,
            "status": "FILLED",
            "filled_quantity": quantity,
            "avg_fill_price": fill_price,
            "fee": fee,
            "notional": notional,
            "rejection_reason": None,
        }

    @staticmethod
    def _compute_fill(
        *,
        side: Literal["BUY", "SELL"],
        order_type: Literal["MARKET", "LIMIT"],
        price: float | None,
        ref_price: float,
    ) -> float | None:
        """返回成交价；返回 ``None`` 表示限价单未触发。"""
        if order_type == "MARKET":
            return ref_price

        # LIMIT：必有 price（schema 已校验）
        assert price is not None
        if side == "BUY":
            if price >= ref_price:
                return min(price, ref_price)
            return None
        # SELL
        if price <= ref_price:
            return max(price, ref_price)
        return None
