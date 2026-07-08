"""E1 种子策略源码 —— 硬编码 SMACrossStrategy。

比 ``archetypes.momentum_trend`` 更简单：无 volume filter、无 position_pct sizing。
LLM 可通过变异添加这些功能。
"""
from __future__ import annotations

from typing import Final

SEED_STRATEGY_CODE: Final[str] = """
from collections import deque
from uuid import uuid4

from inalpha_paper.model.orders import Order, OrderSide, OrderType, ClientOrderId
from inalpha_paper.model.strategy import Strategy


class SMACrossStrategy(Strategy):
    \"\"\"简单双均线交叉策略。

    快线穿过慢线上方 → 做多，快线穿过慢线下 → 平仓。
    \"\"\"

    def __init__(
        self,
        name,
        clock,
        msgbus,
        instrument_id,
        timeframe="1h",
        fast_period=10,
        slow_period=30,
        trade_size=0.01,
    ):
        super().__init__(name, clock, msgbus)
        self._instrument_id = instrument_id
        self._timeframe = timeframe
        self._fast = fast_period
        self._slow = slow_period
        self._trade_size = trade_size
        self._closes = deque(maxlen=slow_period)
        self._prev_fast = None
        self._prev_slow = None
        self._is_long = False
        self._open_qty = 0.0

    def on_start(self):
        self.subscribe_bars(self._instrument_id, self._timeframe)

    def on_bar(self, bar):
        if bar.instrument_id != self._instrument_id or bar.timeframe != self._timeframe:
            return
        self._closes.append(bar.close)
        if len(self._closes) < self._slow:
            return
        fast = sum(list(self._closes)[-self._fast:]) / self._fast
        slow = sum(self._closes) / self._slow
        if self._prev_fast is not None and self._prev_slow is not None:
            if self._prev_fast <= self._prev_slow and fast > slow and not self._is_long:
                self._submit(OrderSide.BUY, bar)
            elif self._prev_fast >= self._prev_slow and fast < slow and self._is_long:
                self._submit(OrderSide.SELL, bar)
        self._prev_fast = fast
        self._prev_slow = slow

    def on_position_opened(self, event):
        self._is_long = event.quantity > 0
        self._open_qty = abs(event.quantity)

    def on_position_closed(self, event):
        self._is_long = False
        self._open_qty = 0.0

    def _submit(self, side, bar):
        if side == OrderSide.SELL and self._open_qty > 0:
            qty = self._open_qty
        else:
            qty = self._trade_size
        order = Order(
            client_order_id=ClientOrderId("sma-" + uuid4().hex[:8]),
            instrument_id=self._instrument_id,
            side=side,
            type=OrderType.MARKET,
            quantity=qty,
        )
        self.submit_order(order)
""".strip()
