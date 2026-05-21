"""端到端 backtest 测试 —— SMA cross 跑完整闭环。

测试目标（D-5 验收标准）：
**用合成数据跑通 K 线 → 策略 → 信号 → 撮合 → 仓位变化 → PnL 报告整条路径**。
"""
from __future__ import annotations

import math

from quant_lab_paper.engine.backtest import BacktestEngine
from quant_lab_paper.engine.report import BacktestReport
from quant_lab_paper.kernel.identifiers import InstrumentId
from quant_lab_paper.model.data import Bar
from quant_lab_paper.strategies.sma_cross import SMACrossStrategy


def _btc() -> InstrumentId:
    return InstrumentId(symbol="BTC/USDT", venue="binance")


def _bar(open: float, high: float, low: float, close: float, ts_ns: int) -> Bar:
    return Bar(
        instrument_id=_btc(),
        timeframe="1h",
        open=open,
        high=high,
        low=low,
        close=close,
        volume=1.0,
        ts_event=ts_ns,
        ts_init=ts_ns,
    )


def _gen_bars(prices: list[float], step_ns: int = 3600 * 1_000_000_000) -> list[Bar]:
    """从一串 close 价格生成 bars。open=high=low=close=p（简化撮合）。"""
    return [
        _bar(open=p, high=p, low=p, close=p, ts_ns=(i + 1) * step_ns)
        for i, p in enumerate(prices)
    ]


# ─── 端到端：sinusoidal 价格 + SMA cross ───


def test_backtest_sma_cross_on_oscillating_prices() -> None:
    """振荡价格 + SMA cross → 触发多次交易，验证完整链路连通。

    用 sin 波合成价格，让 SMA cross 必然交叉多次（每次 ~波长/2）。
    """
    # 价格 = 100 + 10 * sin(2*pi*i/20)，20 周期，跑 5 个周期 = 100 bars
    prices = [100 + 10 * math.sin(2 * math.pi * i / 20) for i in range(100)]
    bars = _gen_bars(prices)

    engine = BacktestEngine(initial_cash=10_000.0, fee_rate=0.001)
    strat = SMACrossStrategy(
        name="sma",
        clock=engine.clock,
        msgbus=engine.msgbus,
        instrument_id=_btc(),
        timeframe="1h",
        fast_period=5,
        slow_period=15,
        trade_size=0.05,
    )
    engine.add_strategy(strat)

    report = engine.run(bars)

    # 报告字段就位
    assert isinstance(report, BacktestReport)
    assert report.num_bars_processed == 100
    assert report.period_start is not None
    assert report.period_end is not None
    # SMA cross 在振荡市必然触发若干次（保守下界 2）
    assert report.num_trades >= 2
    # 振荡价格 + 手续费，策略大概率亏一点点；但 equity 应该接近初始
    assert 9_000 <= report.final_equity <= 11_000
    # 至少有一次 BUY 信号被发出
    assert strat.signal_count >= 2


# ─── 上涨趋势：买入持有 ───


def test_backtest_uptrend_profitable() -> None:
    """单调上涨趋势 → 金叉买入后持有 → 报告应该是正收益。"""
    # 价格先平稳再上涨：让快线必然上穿慢线
    prices = [100.0] * 20 + [100.0 + i * 0.5 for i in range(1, 30)]
    bars = _gen_bars(prices)

    engine = BacktestEngine(initial_cash=10_000.0, fee_rate=0.0)  # 零费率，专测策略
    strat = SMACrossStrategy(
        name="up",
        clock=engine.clock,
        msgbus=engine.msgbus,
        instrument_id=_btc(),
        timeframe="1h",
        fast_period=3,
        slow_period=8,
        trade_size=1.0,
    )
    engine.add_strategy(strat)
    report = engine.run(bars)

    # 单调上涨 + 金叉买入 + 持有到结束 → 正收益
    assert report.num_trades >= 1
    assert report.total_return_pct > 0


# ─── 下跌趋势：不入场或快速出场 ───


def test_backtest_downtrend_no_buy_signals() -> None:
    """单调下跌趋势：快线在慢线下方，永远不会金叉 → 不下单。"""
    prices = [100.0 - i * 0.5 for i in range(60)]
    bars = _gen_bars(prices)

    engine = BacktestEngine(initial_cash=10_000.0)
    strat = SMACrossStrategy(
        name="down",
        clock=engine.clock,
        msgbus=engine.msgbus,
        instrument_id=_btc(),
        timeframe="1h",
        fast_period=3,
        slow_period=8,
    )
    engine.add_strategy(strat)
    report = engine.run(bars)

    assert report.num_trades == 0
    assert report.final_equity == 10_000.0


# ─── 空 bars 报错 ───


def test_empty_bars_raises() -> None:
    engine = BacktestEngine()
    import pytest

    with pytest.raises(ValueError, match="at least one bar"):
        engine.run([])
