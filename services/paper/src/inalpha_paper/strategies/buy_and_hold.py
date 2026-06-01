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
        position_pct: float | None = None,
        initial_cash: float = 0.0,
    ) -> None:
        super().__init__(name, clock, msgbus)
        self._instrument_id = instrument_id
        self._timeframe = timeframe
        self._trade_size = trade_size
        self._position_pct = position_pct
        self._initial_cash = initial_cash
        self._bought: bool = False

    def _resolve_quantity(self, bar: Bar) -> float:
        """按 position_pct 算"满仓比例" qty；缺参数时回退 trade_size 绝对量。

        基准价用 ``bar.close``（信号 bar 的收盘），撮合发生在**下一根** bar.open。
        + 5% buffer 抗 bar-to-bar 价格 jitter（振荡市单根 K 线跳变可达 3-5%）+
        fee + 滑点。"满仓" 实际是 95% 本金，剩 5% 留 cushion 避免撮合层守门拒。
        """
        if (
            self._position_pct is not None
            and self._position_pct > 0
            and self._initial_cash > 0
            and bar.close > 0
        ):
            return (self._initial_cash * self._position_pct) / bar.close / (1.0 + 0.05)
        return self._trade_size

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
            quantity=self._resolve_quantity(bar),
        )
        self.submit_order(order)
        self._bought = True
