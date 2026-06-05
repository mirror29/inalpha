"""ATR / Keltner 通道突破策略 —— 波动率自适应基线（family='volatility'）。

规则：

- 中轨 = ``period`` 根收盘价 SMA；上轨 = 中轨 + ``atr_mult`` × ATR(period)
- 收盘价上破上轨 → 买入开多（波动放大方向突破）
- 收盘价跌回中轨下方 → 卖出平多
- 单标的、全仓位（或按 position_pct 本金比例）

与 ``sma_cross`` 同级——**基线 / 教学样本**，给 compose 的 volatility family 一个可路由出口
（docs/miro/11 M4）。通道宽度随 ATR 自适应：波动大时通道宽、不易被噪声触发。

ATR / 均线都用**历史 N 根**（决策前 append），避免用当根自身 = lookahead。
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


class ATRChannelStrategy(Strategy):
    """ATR/Keltner 通道突破单标的策略。"""

    def __init__(
        self,
        name: str,
        clock: Clock,
        msgbus: MessageBus,
        instrument_id: InstrumentId,
        timeframe: str = "1h",
        period: int = 20,
        atr_mult: float = 2.0,
        trade_size: float = 0.01,
        position_pct: float | None = None,
        initial_cash: float = 0.0,
    ) -> None:
        if period < 2:
            raise ValueError(f"period ({period}) must be >= 2")
        if atr_mult <= 0:
            raise ValueError(f"atr_mult ({atr_mult}) must be > 0")

        super().__init__(name, clock, msgbus)
        self._instrument_id = instrument_id
        self._timeframe = timeframe
        self._period = period
        self._atr_mult = atr_mult
        self._trade_size = trade_size
        self._position_pct = position_pct
        self._initial_cash = initial_cash

        self._closes: deque[float] = deque(maxlen=period)
        self._prev_close: float | None = None
        # Wilder RMA ATR（标准 Keltner 定义，非 SMA）：先用前 period 根 TR 的均值做种子，
        # 之后 atr = (atr*(period-1) + tr) / period 递推。
        self._atr: float | None = None
        self._tr_count: int = 0
        self._tr_seed_sum: float = 0.0

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

        # 用历史 deque + 已递推 ATR 决策（都不含当根 → 无 lookahead）
        if len(self._closes) >= self._period and self._atr is not None:
            mid = sum(self._closes) / len(self._closes)
            upper = mid + self._atr_mult * self._atr
            if not self._is_long and bar.close > upper:
                self._submit_market(OrderSide.BUY, bar)
                self.signal_count += 1
            elif self._is_long and bar.close < mid:
                self._submit_market(OrderSide.SELL, bar)
                self.signal_count += 1

        # 更新 ATR（Wilder RMA）/ 收盘历史（用当根，供下一根决策）
        if self._prev_close is not None:
            tr = max(
                bar.high - bar.low,
                abs(bar.high - self._prev_close),
                abs(bar.low - self._prev_close),
            )
            self._update_atr(tr)
        self._closes.append(bar.close)
        self._prev_close = bar.close

    def _update_atr(self, tr: float) -> None:
        """Wilder RMA 递推 ATR：前 period 根 TR 均值做种子，之后指数式平滑。"""
        if self._atr is None:
            self._tr_count += 1
            self._tr_seed_sum += tr
            if self._tr_count >= self._period:
                self._atr = self._tr_seed_sum / self._period
        else:
            self._atr = (self._atr * (self._period - 1) + tr) / self._period

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
            client_order_id=ClientOrderId(f"atr-{self.name}-{uuid4().hex[:8]}"),
            instrument_id=self._instrument_id,
            side=side,
            type=OrderType.MARKET,
            quantity=qty,
        )
        self.submit_order(order)
