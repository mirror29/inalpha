"""``BacktestEngine`` —— 把所有组件接起来跑完整闭环。

主循环（每根 bar）：

```
for bar in bars:
    1. exchange.process_bar(bar)             ← 撮合上一轮提交的 pending orders
    2. clock.set_time(bar.ts_event)          ← 推进时间到 bar close
    3. portfolio.update_mark(...)            ← 更新 mark price
    4. msgbus.publish('data.bars...', bar)   ← 触发 strategy.on_bar
    5. strategy 可能提交新订单 → 进入 pending，下一根 bar 撮合
```

**先 process_bar 再 publish**：保证策略在 bar N 提交的订单在 bar N+1 撮合，不偷未来。

D-5 阶段简化：

- 单进程同步执行
- 单 strategy 单 instrument（多策略 / 多标的能跑但没专门测试过）
- 不收盘强平（最后剩仓位的 PnL 用最后 mark 估）
"""
from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from ..execution.exchange import SimulatedExchange
from ..execution.execution_engine import ExecutionEngine
from ..execution.risk_engine import RiskEngine
from ..kernel.clock import TestClock
from ..kernel.msgbus import MessageBus
from ..model.data import Bar
from ..strategy.base import Strategy
from .portfolio import Portfolio
from .report import BacktestReport


class BacktestEngine:
    """回测引擎。"""

    def __init__(
        self,
        initial_cash: float = 10_000.0,
        fee_rate: float = 0.001,
    ) -> None:
        # 内核
        self.clock = TestClock(0)
        self.msgbus = MessageBus()

        # 执行链（注册顺序：endpoint 先注册，否则 RiskEngine forward 会抛 KeyError）
        self.exchange = SimulatedExchange(self.msgbus, self.clock)
        self.execution_engine = ExecutionEngine(self.msgbus, self.exchange)
        self.risk_engine = RiskEngine(self.msgbus)
        self.portfolio = Portfolio(self.msgbus, initial_cash=initial_cash, fee_rate=fee_rate)

        self._strategies: list[Strategy] = []
        self._num_bars: int = 0

    def add_strategy(self, strategy: Strategy) -> None:
        """挂载策略。strategy 构造时必须传 ``engine.clock`` / ``engine.msgbus``。"""
        self._strategies.append(strategy)

    def run(self, bars: Iterable[Bar]) -> BacktestReport:
        """跑回测，返回 ``BacktestReport``。"""
        bars_list = list(bars)
        if not bars_list:
            raise ValueError("backtest needs at least one bar")

        # 初始化时间（第一根 bar 之前），便于 strategy.on_start 时拿 clock.now
        first_ts = bars_list[0].ts_event
        if first_ts > 0:
            self.clock.set_time(first_ts)

        for s in self._strategies:
            s.on_start()

        for bar in bars_list:
            # 1. 撮合上一根 bar 之后提交的 pending orders（在当前 bar 撮合）
            self.exchange.process_bar(bar)
            # 2. 推进时间到 bar close
            if bar.ts_event > self.clock.now_ns():
                self.clock.set_time(bar.ts_event)
            # 3. 更新 mark price（让 portfolio.equity() 准确）
            self.portfolio.update_mark(bar.instrument_id, bar.close)
            # 4. 发布 bar，触发 strategy.on_bar
            topic = (
                f"data.bars.{bar.instrument_id.venue}."
                f"{bar.instrument_id.symbol}.{bar.timeframe}"
            )
            self.msgbus.publish(topic, bar)
            # 5. 记 equity curve（含本根 bar 上策略发单后的最新 mark；下一根 bar 撮合后会再更新一次同 ts 的快照）
            self.portfolio.snapshot(bar.ts_event)

            self._num_bars += 1

        for s in self._strategies:
            s.on_stop()

        return self._build_report(bars_list)

    def _build_report(self, bars: list[Bar]) -> BacktestReport:
        timeframe = bars[0].timeframe if bars else "1h"
        return BacktestReport.from_portfolio(
            portfolio=self.portfolio,
            num_bars=self._num_bars,
            period_start=_ts_to_dt(bars[0].ts_event) if bars else None,
            period_end=_ts_to_dt(bars[-1].ts_event) if bars else None,
            timeframe=timeframe,
        )


def _ts_to_dt(ts_ns: int) -> datetime:
    from datetime import UTC

    return datetime.fromtimestamp(ts_ns / 1_000_000_000, tz=UTC)
