"""命令对象 —— Strategy → RiskEngine / ExecutionEngine 走 endpoint，载体就是这些命令。

与事件（events.py）的差异：

- **命令**走 ``msgbus.send(endpoint, cmd)`` 单一处理者（点对点）
- **事件**走 ``msgbus.publish(topic, evt)`` 多订阅者（pub/sub）
"""
from __future__ import annotations

from dataclasses import dataclass

from ..kernel.identifiers import ClientOrderId, StrategyId
from .orders import Order


@dataclass(frozen=True, slots=True)
class SubmitOrderCommand:
    """Strategy 请求下单。``endpoint = "RiskEngine.execute"``。"""

    order: Order
    strategy_id: StrategyId
    ts_init: int


@dataclass(frozen=True, slots=True)
class CancelOrderCommand:
    """Strategy 请求撤单。``endpoint = "RiskEngine.execute"``。"""

    client_order_id: ClientOrderId
    strategy_id: StrategyId
    ts_init: int
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class ModifyOrderCommand:
    """Strategy 请求改单。``endpoint = "RiskEngine.execute"``。"""

    client_order_id: ClientOrderId
    strategy_id: StrategyId
    ts_init: int
    new_quantity: float | None = None
    new_price: float | None = None
