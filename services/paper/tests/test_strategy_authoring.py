"""D-9 · LLM 自创策略沙盒单测。

覆盖：

- ``ast_audit`` 10+ 攻击向量 + 合法用例放行
- ``contract_check`` 协议违反 + 通过
- ``dynamic_loader`` 加载 happy / 找不到 / 多个子类 / compile 失败
- ``fitness`` 多目标合成公式（含 30% 回撤一票否决）
- 端到端：LLM 风格的 SMA cross 副本走完整 audit → load → contract → 跑回测
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from inalpha_paper.engine.backtest import BacktestEngine
from inalpha_paper.kernel.identifiers import InstrumentId
from inalpha_paper.model.data import Bar
from inalpha_paper.strategy_authoring import (
    ContractError,
    DynamicLoadError,
    FitnessInputs,
    audit_strategy_code,
    compose_fitness,
    load_strategy_class,
    verify_strategy_contract,
)
from inalpha_paper.strategy_authoring.fitness import calmar_from_report

# ────────────────────────────────────────────────────────────────────
# AST 审计：攻击向量
# ────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("code", "expected_finding_code"),
    [
        ("import os", "IMPORT_DENIED"),
        ("import sys", "IMPORT_DENIED"),
        ("import socket", "IMPORT_DENIED"),
        ("import subprocess", "IMPORT_DENIED"),
        ("import requests", "IMPORT_DENIED"),
        ("from subprocess import run", "IMPORT_DENIED"),
        ("from os import system", "IMPORT_DENIED"),
        ("from urllib import request", "IMPORT_DENIED"),
        # 相对 import：LLM 写策略不在包内
        ("from . import x", "IMPORT_DENIED"),
        # 动态执行 / 反射
        ("eval('1+1')", "NAME_DENIED"),
        ("exec('print(1)')", "NAME_DENIED"),
        ("compile('1', '<x>', 'eval')", "NAME_DENIED"),
        ("__import__('os')", "NAME_DENIED"),
        ("getattr(object, '__bases__')", "NAME_DENIED"),
        ("setattr(object, 'x', 1)", "NAME_DENIED"),
        ("globals()", "NAME_DENIED"),
        ("locals()", "NAME_DENIED"),
        ("open('/etc/passwd')", "NAME_DENIED"),
        # 经典 dunder 越狱
        ("().__class__.__bases__[0].__subclasses__()", "DUNDER_ACCESS"),
        ("x = (1).__class__", "DUNDER_ACCESS"),
        # global / nonlocal / async
        ("def f():\n    global x\n    x = 1", "GLOBAL_DENIED"),
        ("async def f(): pass", "ASYNC_DENIED"),
    ],
)
def test_audit_rejects_dangerous_code(
    code: str, expected_finding_code: str
) -> None:
    result = audit_strategy_code(code)
    assert not result.ok, f"expected reject: {code!r}"
    codes = {f.code for f in result.findings}
    assert expected_finding_code in codes, f"got findings={codes!r} for {code!r}"


def test_audit_rejects_syntax_error() -> None:
    result = audit_strategy_code("def f(:\n    pass")
    assert not result.ok
    assert result.findings[0].code == "SYNTAX_ERROR"


# ────────────────────────────────────────────────────────────────────
# AST 审计：合法用例
# ────────────────────────────────────────────────────────────────────


def test_audit_allows_stdlib_whitelist() -> None:
    code = """
import math
import statistics
from collections import deque
from dataclasses import dataclass
from typing import Any

x = math.sqrt(4.0)
"""
    assert audit_strategy_code(code).ok


def test_audit_allows_strategy_skeleton() -> None:
    """LLM 风格的最小策略源码 —— 不需要任何 import，符号由 globals 注入。"""
    code = """
class MyStrategy(Strategy):
    def __init__(self, name, clock, msgbus, instrument_id, timeframe="1h"):
        super().__init__(name, clock, msgbus)
        self._instrument_id = instrument_id

    def on_bar(self, bar):
        pass
"""
    result = audit_strategy_code(code)
    assert result.ok, result.reason()


# ────────────────────────────────────────────────────────────────────
# dynamic_loader
# ────────────────────────────────────────────────────────────────────


_MIN_STRATEGY = """
class MyStrategy(Strategy):
    def __init__(self, name, clock, msgbus, instrument_id, timeframe="1h", trade_size=0.01):
        super().__init__(name, clock, msgbus)
        self._instrument_id = instrument_id
        self._timeframe = timeframe
        self._trade_size = trade_size
        self.bar_count = 0

    def on_start(self):
        self.subscribe_bars(self._instrument_id, self._timeframe)

    def on_bar(self, bar):
        self.bar_count += 1
"""


def test_loader_returns_strategy_subclass() -> None:
    cls = load_strategy_class(_MIN_STRATEGY)
    assert cls.__name__ == "MyStrategy"


def test_loader_rejects_no_strategy_subclass() -> None:
    with pytest.raises(DynamicLoadError, match="没有继承 Strategy"):
        load_strategy_class("class NotAStrategy:\n    pass")


def test_loader_rejects_multiple_strategy_subclasses() -> None:
    code = """
class A(Strategy):
    def on_bar(self, bar): pass
class B(Strategy):
    def on_bar(self, bar): pass
"""
    with pytest.raises(DynamicLoadError, match="多个 Strategy 子类"):
        load_strategy_class(code)


def test_loader_rejects_compile_failure() -> None:
    # ast.parse 通过但 compile 报错：return 在 module-level
    with pytest.raises(DynamicLoadError):
        load_strategy_class("return 1")


def test_loader_rejects_runtime_error_in_class_body() -> None:
    # exec 阶段类体里就抛——加载失败
    code = """
class MyStrategy(Strategy):
    x = 1 / 0
    def on_bar(self, bar): pass
"""
    with pytest.raises(DynamicLoadError):
        load_strategy_class(code)


# ────────────────────────────────────────────────────────────────────
# contract_check
# ────────────────────────────────────────────────────────────────────


def test_contract_passes_valid_strategy() -> None:
    cls = load_strategy_class(_MIN_STRATEGY)
    verify_strategy_contract(cls)  # 不抛


def test_contract_rejects_missing_on_bar() -> None:
    code = """
class NoOnBarStrategy(Strategy):
    def __init__(self, name, clock, msgbus, instrument_id, timeframe="1h"):
        super().__init__(name, clock, msgbus)
"""
    cls = load_strategy_class(code)
    with pytest.raises(ContractError, match="覆写 on_bar"):
        verify_strategy_contract(cls)


def test_contract_rejects_missing_init_kwargs() -> None:
    code = """
class WrongInitStrategy(Strategy):
    def __init__(self, name, clock, msgbus):
        super().__init__(name, clock, msgbus)
    def on_bar(self, bar): pass
"""
    cls = load_strategy_class(code)
    with pytest.raises(ContractError, match="缺少必要参数"):
        verify_strategy_contract(cls)


def test_contract_accepts_kwargs_catchall() -> None:
    """``**kwargs`` 兜底是 MVP 允许的（虽然不推荐）。"""
    code = """
class KwargsStrategy(Strategy):
    def __init__(self, name, clock, msgbus, **kwargs):
        super().__init__(name, clock, msgbus)
        self._instrument_id = kwargs["instrument_id"]
    def on_bar(self, bar): pass
"""
    cls = load_strategy_class(code)
    verify_strategy_contract(cls)  # 不抛


# ────────────────────────────────────────────────────────────────────
# fitness 多目标合成
# ────────────────────────────────────────────────────────────────────


def test_fitness_neutral_when_all_none() -> None:
    # 全空 = 0 + 0 - 0 - 0 = 0
    f = compose_fitness(
        FitnessInputs(
            sharpe=None,
            calmar=None,
            max_drawdown_pct=0.0,
            num_trades=0,
            num_bars_processed=0,
        )
    )
    assert f == 0.0


def test_fitness_rewards_sharpe_and_calmar() -> None:
    f = compose_fitness(
        FitnessInputs(
            sharpe=2.0,
            calmar=1.0,
            max_drawdown_pct=10.0,  # < 30% → 不触发否决
            num_trades=10,
            num_bars_processed=1000,
        )
    )
    # 2.0 + 0.3*1.0 - 0.10 * (10/1000*100) = 2.0 + 0.3 - 0.1 = 2.2
    assert f == pytest.approx(2.2, abs=1e-6)


def test_fitness_drawdown_veto_kicks_in_above_30pct() -> None:
    f_high = compose_fitness(
        FitnessInputs(
            sharpe=3.0,
            calmar=2.0,
            max_drawdown_pct=35.0,  # > 30% → 扣 1
            num_trades=1,
            num_bars_processed=1000,
        )
    )
    # 3.0 + 0.6 - 0.01 - 1.0 = 2.59
    assert f_high == pytest.approx(2.59, abs=1e-6)


def test_calmar_from_report_basic() -> None:
    # 1 年 100 bars, return=20%, dd=10% → annual_return=20% → calmar=2.0
    calmar = calmar_from_report(
        total_return_pct=20.0,
        max_drawdown_pct=10.0,
        num_bars_processed=100,
        bars_per_year=100,
    )
    assert calmar == pytest.approx(2.0, abs=1e-6)


def test_calmar_returns_none_when_no_drawdown() -> None:
    assert (
        calmar_from_report(
            total_return_pct=20.0,
            max_drawdown_pct=0.0,
            num_bars_processed=100,
            bars_per_year=100,
        )
        is None
    )


# ────────────────────────────────────────────────────────────────────
# 端到端：LLM 风格 SMA cross 副本跑回测
# ────────────────────────────────────────────────────────────────────


_LLM_SMA_SOURCE = """
class LLMSmaCross(Strategy):
    \"\"\"LLM 风格 SMA cross：快慢均线交叉，全用 globals 注入的符号，零 import。\"\"\"

    def __init__(
        self,
        name,
        clock,
        msgbus,
        instrument_id,
        timeframe="1h",
        fast_period=3,
        slow_period=6,
        trade_size=0.01,
    ):
        if fast_period >= slow_period:
            raise ValueError("fast must < slow")
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

    def on_start(self):
        self.subscribe_bars(self._instrument_id, self._timeframe)

    def on_bar(self, bar):
        if bar.instrument_id != self._instrument_id:
            return
        self._closes.append(bar.close)
        if len(self._closes) < self._slow:
            return
        fast = sum(list(self._closes)[-self._fast:]) / self._fast
        slow = sum(self._closes) / self._slow
        if self._prev_fast is not None and self._prev_slow is not None:
            crossed_up = self._prev_fast <= self._prev_slow and fast > slow
            crossed_down = self._prev_fast >= self._prev_slow and fast < slow
            if crossed_up and not self._is_long:
                self._submit(OrderSide.BUY)
            elif crossed_down and self._is_long:
                self._submit(OrderSide.SELL)
        self._prev_fast = fast
        self._prev_slow = slow

    def on_position_opened(self, event):
        self._is_long = event.quantity > 0

    def on_position_closed(self, event):
        self._is_long = False

    def _submit(self, side):
        order = Order(
            client_order_id=ClientOrderId("llm-" + uuid4().hex[:8]),
            instrument_id=self._instrument_id,
            side=side,
            type=OrderType.MARKET,
            quantity=self._trade_size,
        )
        self.submit_order(order)
"""


def _build_sine_bars(n: int = 50) -> tuple[InstrumentId, list[Bar]]:
    """生成一段正弦波 K 线，便于触发 SMA 交叉信号。"""
    import math

    instrument_id = InstrumentId(symbol="BTC/USDT", venue="binance")
    base_ts = int(
        datetime(2026, 1, 1, tzinfo=UTC).timestamp() * 1_000_000_000
    )
    interval_ns = int(timedelta(hours=1).total_seconds() * 1_000_000_000)
    bars: list[Bar] = []
    for i in range(n):
        price = 100.0 + 10.0 * math.sin(i * 0.4)
        bars.append(
            Bar(
                instrument_id=instrument_id,
                timeframe="1h",
                open=price,
                high=price + 0.5,
                low=price - 0.5,
                close=price,
                volume=1.0,
                ts_event=base_ts + i * interval_ns,
                ts_init=base_ts + i * interval_ns,
            )
        )
    return instrument_id, bars


def test_end_to_end_llm_strategy_runs_backtest() -> None:
    """LLM 写的策略走完整 audit → load → contract → 实例化 → engine.run 链路。"""
    # 1. 沙盒
    audit = audit_strategy_code(_LLM_SMA_SOURCE)
    assert audit.ok, audit.reason()
    cls = load_strategy_class(_LLM_SMA_SOURCE)
    verify_strategy_contract(cls)
    assert cls.__name__ == "LLMSmaCross"

    # 2. 实例化 + 跑
    engine = BacktestEngine(initial_cash=10_000.0, fee_rate=0.001)
    instrument_id, bars = _build_sine_bars(n=60)
    strategy = cls(  # type: ignore[call-arg]
        name="llm-sma-BTC",
        clock=engine.clock,
        msgbus=engine.msgbus,
        instrument_id=instrument_id,
        timeframe="1h",
        fast_period=3,
        slow_period=6,
        trade_size=0.01,
    )
    engine.add_strategy(strategy)
    report = engine.run(bars)

    # 3. 跑通 + 有信号
    assert report.num_bars_processed == 60
    # 正弦波 60 根，fast=3 slow=6 应该触发若干次交叉
    assert report.num_trades > 0, "LLM SMA cross 应至少触发 1 次交易"


# ────────────────────────────────────────────────────────────────────
# D-9 重新定位：baseline 字段 + buy_and_hold 自动并跑
# ────────────────────────────────────────────────────────────────────


def test_baseline_buy_and_hold_runs_on_same_bars() -> None:
    """BASELINE_BUY_AND_HOLD 名字常量能拿到 BuyAndHoldStrategy，并能跑同 bars。

    验证 candidate 路径下 runner 并跑 baseline 的核心组件可用：
    1. BASELINE_BUY_AND_HOLD 名字常量解析正确
    2. 同 bars 同 cash 跑 buy_and_hold 拿到非空 report
    3. _fitness_from_report 在 baseline report 上也能算
    """
    from inalpha_paper.engine.metrics import periods_per_year
    from inalpha_paper.runner import _fitness_from_report
    from inalpha_paper.strategies import BASELINE_BUY_AND_HOLD, get_strategy_class

    assert BASELINE_BUY_AND_HOLD == "buy_and_hold"
    cls = get_strategy_class(BASELINE_BUY_AND_HOLD)

    engine = BacktestEngine(initial_cash=10_000.0, fee_rate=0.001)
    instrument_id, bars = _build_sine_bars(n=30)
    strategy = cls(  # type: ignore[call-arg]
        name="baseline-bh",
        clock=engine.clock,
        msgbus=engine.msgbus,
        instrument_id=instrument_id,
        timeframe="1h",
        trade_size=0.5,
    )
    engine.add_strategy(strategy)
    report = engine.run(bars)

    # buy_and_hold 必有 1 次 BUY（第一根 bar 后）
    assert report.num_trades == 1, "buy_and_hold 应在第一根 bar 后下单一次"
    # fitness 能算（即使 sharpe 可能为 None，compose_fitness 用 0 兜底）
    fitness = _fitness_from_report(report, bars_per_year=float(periods_per_year("1h")))
    assert isinstance(fitness, float)


def test_baseline_snapshot_schema_round_trip() -> None:
    """BaselineSnapshot Pydantic schema 字段齐全 + 可序列化。"""
    from inalpha_paper.schemas import BaselineSnapshot

    snap = BaselineSnapshot(
        strategy_id="buy_and_hold",
        fitness=0.42,
        sharpe=1.2,
        max_drawdown_pct=8.5,
        total_return_pct=15.0,
        num_trades=1,
    )
    dumped = snap.model_dump()
    assert dumped["strategy_id"] == "buy_and_hold"
    assert dumped["fitness"] == 0.42
    # 反序列化也通
    rebuilt = BaselineSnapshot(**dumped)
    assert rebuilt == snap
