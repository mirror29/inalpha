"""Gateway 抽象 —— 经纪商 / 交易所接入。

设计依据 [refs/vnpy.md §3 §7](../../../../docs/refs/vnpy.md)，把 vnpy 的 7 个抽象方法
裁剪到 MVP 需要的：

- ``send_order`` —— 唯一必须实现
- ``cancel_order`` / ``modify_order`` —— 可选（不实现就 raise NotImplementedError）

D-5 起只有 ``SimulatedExchange`` 一个实现（同时承担 venue 撮合职责）。D-6 起加
``LiveGateway``（CCXT Binance）。
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from ..kernel.identifiers import ClientOrderId, StrategyId
from ..model.orders import Order


class Gateway(ABC):
    """所有 venue / 经纪商接入的统一接口。"""

    @abstractmethod
    def send_order(self, order: Order, strategy_id: StrategyId) -> None:
        """提交订单。同步返回（实际成交回报走 msgbus）。"""

    def cancel_order(self, client_order_id: ClientOrderId) -> None:
        raise NotImplementedError("cancel_order not supported by this gateway")

    def modify_order(
        self,
        client_order_id: ClientOrderId,
        new_quantity: float | None = None,
        new_price: float | None = None,
    ) -> None:
        raise NotImplementedError("modify_order not supported by this gateway")
