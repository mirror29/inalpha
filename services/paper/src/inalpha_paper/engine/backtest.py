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
from ..execution.risk_rules import LockStore, RiskRule
from ..kernel.clock import TestClock
from ..kernel.msgbus import MessageBus
from ..model.data import Bar
from ..strategy.base import Strategy
from .portfolio import Portfolio
from .position_guard import PositionGuard
from .report import BacktestReport


class BacktestEngine:
    """回测引擎。"""

    def __init__(
        self,
        initial_cash: float = 10_000.0,
        fee_rate: float = 0.001,
        *,
        rules: list[RiskRule] | None = None,
        lock_store: LockStore | None = None,
        protective_stop_loss_pct: float | None = None,
        protective_take_profit_pct: float | None = None,
        protective_trailing_stop_pct: float | None = None,
    ) -> None:
        """初始化。

        Args:
            initial_cash: 账户起始现金（也作为 RiskEngine ``starting_balance``，
                让 MaxDrawdownRule 等 global rule 用正确基准）
            fee_rate: 撮合手续费率
            rules: ADR-0006 RiskRule 列表。``None`` 时 RiskEngine 退化为 pass-through
                （向后兼容 D-5 ~ D-8 调用方）
            lock_store: 风控锁存储。None 时 RiskEngine 自动创建 InMemoryLockStore
            protective_stop_loss_pct: ADR-0052 框架级持仓保护止损阈值（None = 关）
            protective_take_profit_pct: 框架级止盈阈值（None = 关）
            protective_trailing_stop_pct: 框架级移动止损阈值（None = 关）
        """
        # 内核
        self.clock = TestClock(0)
        self.msgbus = MessageBus()

        # 执行链（注册顺序：endpoint 先注册，否则 RiskEngine forward 会抛 KeyError）
        self.exchange = SimulatedExchange(self.msgbus, self.clock)
        self.execution_engine = ExecutionEngine(self.msgbus, self.exchange)
        # rules + starting_balance 统一从 BacktestEngine.initial_cash 派生
        self.risk_engine = RiskEngine(
            self.msgbus,
            rules=rules,
            clock=self.clock if rules else None,
            starting_balance=initial_cash,
            lock_store=lock_store,
        )
        self.portfolio = Portfolio(self.msgbus, initial_cash=initial_cash, fee_rate=fee_rate)
        # spot 守门：让 SimulatedExchange 撮合前能 query portfolio cash / position
        # （ADR-0032 BuyingPowerRule 撮合层兜底实现，旧 BTC -98% bug 同源防御）
        self.exchange.bind_portfolio(self.portfolio)

        # ADR-0052：框架级持仓保护止损（与 live session 共用同一组件，行为一致）。
        # 三阈值全 None → from_thresholds 返 None，退化为无 guard（向后兼容）。
        self._guard = PositionGuard.from_thresholds(
            self.msgbus,
            self.clock,
            self.portfolio,
            stop_loss_pct=protective_stop_loss_pct,
            take_profit_pct=protective_take_profit_pct,
            trailing_stop_pct=protective_trailing_stop_pct,
        )

        self._strategies: list[Strategy] = []
        self._num_bars: int = 0

    def add_strategy(self, strategy: Strategy) -> None:
        """挂载策略。strategy 构造时必须传 ``engine.clock`` / ``engine.msgbus``。

        启用了 PositionGuard（protective_* 阈值非空）时**只允许单策略**：guard 只持一个
        strategy_id，多策略会让保护性出场归属错误（前策略 on_position_closed 不触发、状态
        不归零）。挂第二个策略即抛 RuntimeError（CR #88）。需多策略回测请关闭 protective_*。
        """
        if self._guard is not None and self._strategies:
            raise RuntimeError(
                "PositionGuard 启用时只支持单策略 per engine（多策略需先完成引擎多策略化，"
                "CR #88 / ADR-0052 已知限制）；多策略回测请置 protective_* 阈值为 None。"
            )
        self._strategies.append(strategy)
        # guard 出场单用策略自身 id 提交（确保 on_position_closed 回到策略让其状态归零）
        if self._guard is not None:
            self._guard.bind_strategy(strategy.strategy_id)

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
            # 3.5 ADR-0052：框架级持仓保护止损在 mark 更新后判定（与 live session 同点），
            #     触发的保护性出场单进 pending，下一根 process_bar 撮合（不偷未来）。
            #     已知限制（CR #88，仅显式传 rules 的回测受影响、生产 runner rules=None 不触达、
            #     live 顺序路由不受影响）：guard 的 SELL 在 bar N 仅入 pending、bar N+1 才成交，
            #     故同一 bar 内策略经 RiskEngine 提交的 BUY 看不到这次平仓 → CooldownRule 等
            #     基于 closed_trades 的锁在该时间窗查不到记录，可能放过同 bar 重入单（"止损后
            #     不回场"在此窗失效）。要严格联动 rules，用 live 路径（顺序路由先确认 guard 成交）。
            if self._guard is not None:
                self._guard.evaluate(bar)
            # 4. 发布 bar，触发 strategy.on_bar
            topic = (
                f"data.bars.{bar.instrument_id.venue}."
                f"{bar.instrument_id.symbol}.{bar.timeframe}"
            )
            self.msgbus.publish(topic, bar)
            # 5. 记 equity curve（含本根 bar 上策略发单后的最新 mark；下一根 bar 撮合后会再更新一次同 ts 的快照）
            self.portfolio.snapshot(bar.ts_event)

            self._num_bars += 1

        # ADR-0052：末根 bar 触发的保护性出场单没有下一根可撮合（process_bar 用 next-bar
        # open 防 look-ahead）。收尾按末根 close 兜底成交——close 是决策时已知价、非
        # look-ahead，且与 live runner 同根 close 撮合对齐，避免「末根触发 → 漏计 /
        # 持仓显示未平」的回测/live 不一致（CR #88）。只动保护性单，策略单不收盘强平。
        # 限制：按 bars_list[-1] 的 instrument 收尾（沿用引擎「单 instrument per session」
        # 契约）；多标的回测末端非末根 instrument 的保护单不在此 flush——多标的支持是引擎
        # 层整体未做项（CR #88 medium，与引擎多标的化一并推进，非本闸单独修）。
        if self._guard is not None and bars_list:
            if self.exchange.flush_protective_at_close(bars_list[-1]) > 0:
                # 兜底平仓改变了持仓/现金（差一笔手续费），重记末点权益。snapshot 对同 ts
                # 是**覆盖**末点（见 Portfolio.snapshot），不会产生重复 ts / 多算一个 bar。
                self.portfolio.snapshot(bars_list[-1].ts_event)

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
