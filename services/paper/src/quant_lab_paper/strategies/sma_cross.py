"""SMA cross 示例策略 —— 经典快慢均线交叉。

规则：

- 快线（默认 10 周期）上穿慢线 → 买入开多
- 快线下穿慢线 → 卖出平多
- 单标的、全仓位、不止损、不加仓（MVP 起步形态）

测试用法见 ``tests/test_sma_cross.py`` 与 ``tests/test_backtest_e2e.py``。
"""
from __future__ import annotations

from collections import deque
from uuid import uuid4

from ..kernel.clock import Clock
from ..kernel.identifiers import ClientOrderId, InstrumentId
from ..kernel.msgbus import MessageBus
from ..model.data import Bar
from ..model.events import OrderFilled, PositionClosed, PositionOpened
from ..model.orders import Order, OrderSide, OrderType
from ..strategy.base import Strategy


class SMACrossStrategy(Strategy):
    """快慢 SMA 交叉单标的策略。"""

    def __init__(
        self,
        name: str,
        clock: Clock,
        msgbus: MessageBus,
        instrument_id: InstrumentId,
        timeframe: str = "1h",
        fast_period: int = 10,
        slow_period: int = 30,
        trade_size: float = 0.01,
    ) -> None:
        if fast_period >= slow_period:
            raise ValueError(f"fast_period ({fast_period}) must be < slow_period ({slow_period})")

        super().__init__(name, clock, msgbus)
        self._instrument_id = instrument_id
        self._timeframe = timeframe
        self._fast_period = fast_period
        self._slow_period = slow_period
        self._trade_size = trade_size

        # 滚动窗口存收盘价
        self._closes: deque[float] = deque(maxlen=slow_period)
        self._prev_fast: float | None = None
        self._prev_slow: float | None = None

        # 仓位状态（避免重复发单）
        self._is_long: bool = False
        # 计数用于测试
        self.signal_count: int = 0

    def on_start(self) -> None:
        self.subscribe_bars(self._instrument_id, self._timeframe)

    def on_bar(self, bar: Bar) -> None:
        # 关心的 instrument 才处理
        if bar.instrument_id != self._instrument_id:
            return
        if bar.timeframe != self._timeframe:
            return

        self._closes.append(bar.close)
        if len(self._closes) < self._slow_period:
            return  # 预热未完成

        fast = sum(list(self._closes)[-self._fast_period :]) / self._fast_period
        slow = sum(self._closes) / self._slow_period

        if self._prev_fast is not None and self._prev_slow is not None:
            crossed_up = self._prev_fast <= self._prev_slow and fast > slow
            crossed_down = self._prev_fast >= self._prev_slow and fast < slow

            if crossed_up and not self._is_long:
                self._submit_market(OrderSide.BUY)
                self.signal_count += 1
            elif crossed_down and self._is_long:
                self._submit_market(OrderSide.SELL)
                self.signal_count += 1

        self._prev_fast = fast
        self._prev_slow = slow

    def on_order_filled(self, event: OrderFilled) -> None:
        # 跟踪 long/flat 状态由 position event 决定，这里只 log（debug 用）
        pass

    def on_position_opened(self, event: PositionOpened) -> None:
        self._is_long = event.quantity > 0

    def on_position_closed(self, event: PositionClosed) -> None:
        self._is_long = False

    # ─── 内部 ───

    def _submit_market(self, side: OrderSide) -> None:
        order = Order(
            client_order_id=ClientOrderId(f"sma-{self.name}-{uuid4().hex[:8]}"),
            instrument_id=self._instrument_id,
            side=side,
            type=OrderType.MARKET,
            quantity=self._trade_size,
        )
        self.submit_order(order)
