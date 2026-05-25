"""端到端 ``SignalReplayStrategy`` × ``BacktestEngine`` —— E1 闭环验收。

测试 LLM 在沙盒生成的 signals 重放进真 BacktestEngine 跑通：
- 出 BacktestReport（含 metrics）
- 仓位 / 手续费 / equity_curve 行为符合 signals 意图
"""
from __future__ import annotations

from inalpha_paper.engine.backtest import BacktestEngine
from inalpha_paper.engine.report import BacktestReport
from inalpha_paper.kernel.identifiers import InstrumentId
from inalpha_paper.model.data import Bar
from inalpha_paper.strategies.signal_replay import SignalReplayStrategy

_NS_PER_MS = 1_000_000


def _btc() -> InstrumentId:
    return InstrumentId(symbol="BTC/USDT", venue="binance")


def _bars(prices: list[float], start_ms: int = 1_700_000_000_000, step_ms: int = 3_600_000) -> list[Bar]:
    """从价格序列造 bars；ts 单位 ms，构造时转 ns。"""
    return [
        Bar(
            instrument_id=_btc(),
            timeframe="1h",
            open=p, high=p, low=p, close=p, volume=1.0,
            ts_event=(start_ms + i * step_ms) * _NS_PER_MS,
            ts_init=(start_ms + i * step_ms) * _NS_PER_MS,
        )
        for i, p in enumerate(prices)
    ]


# ──────────────────────────────────────────────────────────────────────
# E1 闭环：signals 重放跑出 BacktestReport
# ──────────────────────────────────────────────────────────────────────


def test_signal_replay_buy_then_sell_profits_on_rising_price() -> None:
    """signals = [BUY@bar3, SELL@bar7]，价格 100→110，应产生正收益。"""
    prices = [100, 102, 104, 106, 108, 110, 110, 110, 110, 110]
    bars = _bars(prices)
    # BUY 在第 4 根 bar（index 3），SELL 在第 8 根（index 7）
    bar3_ms = bars[3].ts_event // _NS_PER_MS
    bar7_ms = bars[7].ts_event // _NS_PER_MS
    signals = [
        {"ts": bar3_ms, "side": "BUY", "qty": 0.5},
        {"ts": bar7_ms, "side": "SELL", "qty": 0.5},
    ]

    engine = BacktestEngine(initial_cash=10_000.0, fee_rate=0.001)
    strat = SignalReplayStrategy(
        name="replay-test",
        clock=engine.clock,
        msgbus=engine.msgbus,
        instrument_id=_btc(),
        timeframe="1h",
        signals=signals,
    )
    engine.add_strategy(strat)

    report = engine.run(bars)

    assert isinstance(report, BacktestReport)
    assert report.num_bars_processed == 10
    # 两笔 trade（BUY + SELL）成交
    assert report.num_trades >= 1
    # signals 全消费
    assert strat.replayed_count == 2
    assert strat.initial_signal_count == 2
    # 等价：买 0.5@106 卖 0.5@110 ≈ +2 - 2*手续费(很小) > 0
    assert report.final_equity > report.initial_cash
    # equity_curve 非空
    assert len(report.equity_curve) > 0


def test_signal_replay_empty_signals_no_trades() -> None:
    """空 signals 进去 → 0 笔交易 → final_equity 严格等于 initial_cash。"""
    bars = _bars([100, 101, 102, 103, 104])
    engine = BacktestEngine(initial_cash=10_000.0, fee_rate=0.001)
    strat = SignalReplayStrategy("t", engine.clock, engine.msgbus, _btc(), signals=[])
    engine.add_strategy(strat)
    report = engine.run(bars)
    assert report.num_trades == 0
    assert report.final_equity == 10_000.0
    assert strat.replayed_count == 0


def test_signal_replay_via_registry() -> None:
    """通过 strategies registry 拿到类，模拟 runner.py 的实例化路径。

    这是 prod path 的关键 —— FastAPI 传 ``strategy_id="signal_replay"`` + ``params={signals:...}``
    的时候，runner 用 get_strategy_class + **params 来构造。
    """
    from inalpha_paper.strategies import get_strategy_class, list_strategies

    assert "signal_replay" in list_strategies()
    cls = get_strategy_class("signal_replay")
    assert cls is SignalReplayStrategy

    # 模拟 runner 的实例化方式（**req.params 解包）
    bars = _bars([100, 105, 110])
    bar1_ms = bars[1].ts_event // _NS_PER_MS

    engine = BacktestEngine(initial_cash=10_000.0, fee_rate=0.001)
    params = {"signals": [{"ts": bar1_ms, "side": "BUY", "qty": 0.1}]}
    strat = cls(  # type: ignore[call-arg]
        name="signal_replay-BTC/USDT",
        clock=engine.clock,
        msgbus=engine.msgbus,
        instrument_id=_btc(),
        timeframe="1h",
        **params,
    )
    engine.add_strategy(strat)
    report = engine.run(bars)

    assert report.num_bars_processed == 3
    assert strat.replayed_count == 1
