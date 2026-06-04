"""Donchian 通道突破策略 —— 经典趋势突破基线（family='breakout'）。

规则：

- 收盘价上破前 ``channel_period`` 根 K 线的最高价 → 买入开多
- 收盘价下破前 ``exit_period`` 根 K 线的最低价 → 卖出平多
- 单标的、全仓位（或按 position_pct 本金比例）、不加仓

与 ``sma_cross`` 同级——**基线 / 教学样本**，给 compose 的 breakout family 一个可路由出口
（docs/miro/11 M4）。具体行情下的复杂突破策略请走 ``paper.author_strategy``。

通道用**前 N 根**（决策前 append，避免用当根自身高点 = lookahead）。
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


class DonchianBreakoutStrategy(Strategy):
    """Donchian 通道突破单标的策略。"""

    def __init__(
        self,
        name: str,
        clock: Clock,
        msgbus: MessageBus,
        instrument_id: InstrumentId,
        timeframe: str = "1h",
        channel_period: int = 20,
        exit_period: int = 10,
        trade_size: float = 0.01,
        position_pct: float | None = None,
        initial_cash: float = 0.0,
    ) -> None:
        if channel_period < 2:
            raise ValueError(f"channel_period ({channel_period}) must be >= 2")
        if exit_period < 1:
            raise ValueError(f"exit_period ({exit_period}) must be >= 1")

        super().__init__(name, clock, msgbus)
        self._instrument_id = instrument_id
        self._timeframe = timeframe
        self._channel_period = channel_period
        self._exit_period = exit_period
        self._trade_size = trade_size
        self._position_pct = position_pct
        self._initial_cash = initial_cash

        # 前 N 根高/低（决策前 append，故为"历史通道"，不含当根）
        self._highs: deque[float] = deque(maxlen=channel_period)
        self._lows: deque[float] = deque(maxlen=exit_period)

        self._is_long: bool = False
        self._open_qty: float = 0.0
        self.signal_count: int = 0

    def on_start(self) -> None:
        self.subscribe_bars(self._instrument_id, self._timeframe)

    def on_bar(self, bar: Bar) -> None:
        if bar.instrument_id != self._instrument_id:
            return
        if bar.timeframe != self._timeframe:
            return

        # 通道预热完成后才决策（用历史 deque，不含当根 → 无 lookahead）
        if len(self._highs) >= self._channel_period and len(self._lows) >= self._exit_period:
            upper = max(self._highs)
            lower = min(self._lows)
            if not self._is_long and bar.close > upper:
                self._submit_market(OrderSide.BUY, bar)
                self.signal_count += 1
            elif self._is_long and bar.close < lower:
                self._submit_market(OrderSide.SELL, bar)
                self.signal_count += 1

        self._highs.append(bar.high)
        self._lows.append(bar.low)

    def on_order_filled(self, event: OrderFilled) -> None:
        pass

    def on_position_opened(self, event: PositionOpened) -> None:
        self._is_long = event.quantity > 0
        self._open_qty = abs(event.quantity)

    def on_position_closed(self, event: PositionClosed) -> None:
        self._is_long = False
        self._open_qty = 0.0

    # ─── 内部（与 sma_cross 同语义） ───

    def _resolve_quantity(self, bar: Bar) -> float:
        if (
            self._position_pct is not None
            and self._position_pct > 0
            and self._initial_cash > 0
            and bar.close > 0
        ):
            return (self._initial_cash * self._position_pct) / bar.close / (1.0 + 0.05)
        return self._trade_size

    def _submit_market(self, side: OrderSide, bar: Bar) -> None:
        if side == OrderSide.SELL and self._open_qty > 0:
            qty = self._open_qty
        else:
            qty = self._resolve_quantity(bar)
        order = Order(
            client_order_id=ClientOrderId(f"don-{self.name}-{uuid4().hex[:8]}"),
            instrument_id=self._instrument_id,
            side=side,
            type=OrderType.MARKET,
            quantity=qty,
        )
        self.submit_order(order)
