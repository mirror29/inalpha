"""Buy-and-hold 基准策略 —— 第一根 bar 全仓买入，持有到结束。

用途：

- 任何主动策略的**对照基线**：alpha 是否真的跑赢"什么都不干"
- 验证撮合 / portfolio / 报告链路对趋势市的处理
"""
from __future__ import annotations

from uuid import uuid4

from ..kernel.clock import Clock
from ..kernel.identifiers import ClientOrderId, InstrumentId
from ..kernel.msgbus import MessageBus
from ..model.data import Bar
from ..model.orders import Order, OrderSide, OrderType
from ..strategy.base import Strategy


class BuyAndHoldStrategy(Strategy):
    """第一根 bar 市价买入 ``trade_size``，之后不再交易。"""

    def __init__(
        self,
        name: str,
        clock: Clock,
        msgbus: MessageBus,
        instrument_id: InstrumentId,
        timeframe: str = "1h",
        trade_size: float = 0.01,
    ) -> None:
        super().__init__(name, clock, msgbus)
        self._instrument_id = instrument_id
        self._timeframe = timeframe
        self._trade_size = trade_size
        self._bought: bool = False

    def on_start(self) -> None:
        self.subscribe_bars(self._instrument_id, self._timeframe)

    def on_bar(self, bar: Bar) -> None:
        if self._bought:
            return
        if bar.instrument_id != self._instrument_id or bar.timeframe != self._timeframe:
            return

        order = Order(
            client_order_id=ClientOrderId(f"bh-{self.name}-{uuid4().hex[:8]}"),
            instrument_id=self._instrument_id,
            side=OrderSide.BUY,
            type=OrderType.MARKET,
            quantity=self._trade_size,
        )
        self.submit_order(order)
        self._bought = True
