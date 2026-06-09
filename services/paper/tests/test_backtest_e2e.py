"""端到端 backtest 测试 —— SMA cross 跑完整闭环。

测试目标（D-5 验收标准）：
**用合成数据跑通 K 线 → 策略 → 信号 → 撮合 → 仓位变化 → PnL 报告整条路径**。
"""
from __future__ import annotations

import math

from inalpha_paper.engine.backtest import BacktestEngine
from inalpha_paper.engine.report import BacktestReport
from inalpha_paper.kernel.identifiers import InstrumentId
from inalpha_paper.model.data import Bar
from inalpha_paper.strategies.sma_cross import SMACrossStrategy


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

    # 绩效指标字段（D-7+ 新加）
    assert len(report.equity_curve) == report.num_bars_processed
    # 每个点都是 (ts_ns, equity)
    assert all(isinstance(p[0], int) and isinstance(p[1], float) for p in report.equity_curve)
    # 振荡市最大回撤应该 > 0（有交易必然有回撤）
    assert report.max_drawdown_pct > 0.0
    # Sharpe 应有值（≥2 笔交易 + 100 bar，必然非平稳）
    assert report.sharpe is not None
    # Sortino 同理
    assert report.sortino is not None
    # 振荡市 → 至少有完整 round-trip，胜率有值
    assert report.win_rate is not None
    assert 0.0 <= report.win_rate <= 100.0


# ─── 逐笔成交记录（含每笔盈亏） ───


def test_backtest_records_per_fill_trades() -> None:
    """振荡市 → report.fills 收齐每笔成交，含正确 intent 与每笔实现盈亏。

    验收（D-11+ 详情页「回测成交」）：
    - 每笔 fill 一条记录，条数 == num_trades
    - intent 取值合法，且首笔（空仓 BUY）= open_long
    - 平仓笔 realized_pnl 增量之和 == round-trip closed_trade_pnls 之和（开仓笔=0）
    """
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

    assert report.num_trades >= 2
    # 每笔成交一条记录
    assert len(report.fills) == report.num_trades
    # 字段合理
    for f in report.fills:
        assert f.side in ("BUY", "SELL")
        assert f.intent in ("open_long", "open_short", "close")
        assert f.quantity > 0
        assert f.fill_price > 0
        assert f.fee >= 0
        assert f.order_type == "MARKET"
    # 现货 long-only：首笔必是空仓买入 = 开多
    assert report.fills[0].side == "BUY"
    assert report.fills[0].intent == "open_long"
    # 每笔实现盈亏之和 == round-trip 盈亏之和（开仓笔贡献 0；in-process 跑，portfolio 即最终态）
    assert math.isclose(
        sum(f.realized_pnl for f in report.fills),
        sum(engine.portfolio.closed_trade_pnls),
        rel_tol=1e-9,
        abs_tol=1e-6,
    )


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
    # 没交易 → 没 round-trip → win_rate=None
    assert report.win_rate is None
    # equity 全程平稳 → 0 回撤
    assert report.max_drawdown_pct == 0.0
    # equity 全程平稳 → Sharpe=None（std=0）
    assert report.sharpe is None


# ─── 空 bars 报错 ───


def test_empty_bars_raises() -> None:
    engine = BacktestEngine()
    import pytest

    with pytest.raises(ValueError, match="at least one bar"):
        engine.run([])
