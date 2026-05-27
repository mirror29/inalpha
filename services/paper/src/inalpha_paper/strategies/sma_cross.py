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
        position_pct: float | None = None,
        initial_cash: float = 0.0,
    ) -> None:
        if fast_period >= slow_period:
            raise ValueError(f"fast_period ({fast_period}) must be < slow_period ({slow_period})")

        super().__init__(name, clock, msgbus)
        self._instrument_id = instrument_id
        self._timeframe = timeframe
        self._fast_period = fast_period
        self._slow_period = slow_period
        self._trade_size = trade_size
        # position_pct + initial_cash 同时给且 >0 时按本金比例下单（每根信号
        # bar 实时按当前 bar.open 算 qty）；否则回退到 trade_size 绝对量旧语义。
        self._position_pct = position_pct
        self._initial_cash = initial_cash

        # 滚动窗口存收盘价
        self._closes: deque[float] = deque(maxlen=slow_period)
        self._prev_fast: float | None = None
        self._prev_slow: float | None = None

        # 仓位状态（避免重复发单）
        self._is_long: bool = False
        # 记开仓 qty，平仓时复用（避免 SELL qty 用本金重算与持仓不一致 →
        # 撮合守门 can_afford_sell 拒）
        self._open_qty: float = 0.0
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
                self._submit_market(OrderSide.BUY, bar)
                self.signal_count += 1
            elif crossed_down and self._is_long:
                self._submit_market(OrderSide.SELL, bar)
                self.signal_count += 1

        self._prev_fast = fast
        self._prev_slow = slow

    def on_order_filled(self, event: OrderFilled) -> None:
        # 跟踪 long/flat 状态由 position event 决定，这里只 log（debug 用）
        pass

    def on_position_opened(self, event: PositionOpened) -> None:
        self._is_long = event.quantity > 0
        self._open_qty = abs(event.quantity)

    def on_position_closed(self, event: PositionClosed) -> None:
        self._is_long = False
        self._open_qty = 0.0

    # ─── 内部 ───

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

    def _submit_market(self, side: OrderSide, bar: Bar) -> None:
        # SELL 平仓时用 _open_qty（避免和持仓数量不一致被守门拒）；BUY 时按
        # position_pct / trade_size 算
        if side == OrderSide.SELL and self._open_qty > 0:
            qty = self._open_qty
        else:
            qty = self._resolve_quantity(bar)
        order = Order(
            client_order_id=ClientOrderId(f"sma-{self.name}-{uuid4().hex[:8]}"),
            instrument_id=self._instrument_id,
            side=side,
            type=OrderType.MARKET,
            quantity=qty,
        )
        self.submit_order(order)
