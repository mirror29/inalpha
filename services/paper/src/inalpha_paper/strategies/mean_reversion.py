"""布林带均值回归策略。

逻辑：

- 维护过去 ``period`` 根 bar 的 SMA 和 StdDev
- ``upper = SMA + std_mult * StdDev``，``lower = SMA - std_mult * StdDev``
- ``close < lower`` 且无多仓 → 买入开多（认为超卖）
- ``close > upper`` 且有多仓 → 卖出平多（回归均值）

MVP 单边 long-only，不做空。
"""
from __future__ import annotations

import math
from collections import deque
from uuid import uuid4

from ..kernel.clock import Clock
from ..kernel.identifiers import ClientOrderId, InstrumentId
from ..kernel.msgbus import MessageBus
from ..model.data import Bar
from ..model.events import PositionClosed, PositionOpened
from ..model.orders import Order, OrderSide, OrderType
from ..strategy.base import Strategy


class MeanReversionStrategy(Strategy):
    """布林带均值回归（long-only）。"""

    def __init__(
        self,
        name: str,
        clock: Clock,
        msgbus: MessageBus,
        instrument_id: InstrumentId,
        timeframe: str = "1h",
        period: int = 20,
        std_mult: float = 2.0,
        trade_size: float = 0.01,
        position_pct: float | None = None,
        initial_cash: float = 0.0,
    ) -> None:
        if period < 2:
            raise ValueError(f"period must be >= 2, got {period}")
        if std_mult <= 0:
            raise ValueError(f"std_mult must be positive, got {std_mult}")

        super().__init__(name, clock, msgbus)
        self._instrument_id = instrument_id
        self._timeframe = timeframe
        self._period = period
        self._std_mult = std_mult
        self._trade_size = trade_size
        # 同 sma_cross：position_pct + initial_cash 启用本金比例模式
        self._position_pct = position_pct
        self._initial_cash = initial_cash

        self._closes: deque[float] = deque(maxlen=period)
        self._is_long: bool = False
        self._open_qty: float = 0.0  # 平仓时复用，避免与持仓量不一致被守门拒
        self.signal_count: int = 0

    def on_start(self) -> None:
        self.subscribe_bars(self._instrument_id, self._timeframe)

    def on_bar(self, bar: Bar) -> None:
        if bar.instrument_id != self._instrument_id or bar.timeframe != self._timeframe:
            return

        self._closes.append(bar.close)
        if len(self._closes) < self._period:
            return

        n = float(self._period)
        mean = sum(self._closes) / n
        var = sum((c - mean) ** 2 for c in self._closes) / n
        std = math.sqrt(var)
        upper = mean + self._std_mult * std
        lower = mean - self._std_mult * std

        if bar.close < lower and not self._is_long:
            self._submit_market(OrderSide.BUY, bar)
            self.signal_count += 1
        elif bar.close > upper and self._is_long:
            self._submit_market(OrderSide.SELL, bar)
            self.signal_count += 1

    def on_position_opened(self, event: PositionOpened) -> None:
        self._is_long = event.quantity > 0
        self._open_qty = abs(event.quantity)

    def on_position_closed(self, event: PositionClosed) -> None:
        self._is_long = False
        self._open_qty = 0.0

    def _resolve_quantity(self, bar: Bar) -> float:
        """按 position_pct 算满仓 qty；缺参数时回退 trade_size 绝对量。

        基准价用 ``bar.close``（信号 bar 的收盘）+ 1% buffer，详见 sma_cross。
        """
        if (
            self._position_pct is not None
            and self._position_pct > 0
            and self._initial_cash > 0
            and bar.close > 0
        ):
            return (self._initial_cash * self._position_pct) / bar.close / (1.0 + 0.05)
        return self._trade_size

    def _submit_market(self, side: OrderSide, bar: Bar) -> None:
        # SELL 平仓用 _open_qty；BUY 按本金 / trade_size 算（见 sma_cross 同款逻辑）
        if side == OrderSide.SELL and self._open_qty > 0:
            qty = self._open_qty
        else:
            qty = self._resolve_quantity(bar)
        order = Order(
            client_order_id=ClientOrderId(f"mr-{self.name}-{uuid4().hex[:8]}"),
            instrument_id=self._instrument_id,
            side=side,
            type=OrderType.MARKET,
            quantity=qty,
        )
        self.submit_order(order)
