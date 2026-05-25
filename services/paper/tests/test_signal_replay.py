"""``SignalReplayStrategy`` 单测 —— 验证 signal 重放逻辑（不跑完整 engine）。

测策略类自身：构造校验 + on_bar 触发时机 + 多种边界。
端到端（真跑 BacktestEngine 出 metrics）走 ``test_signal_replay_e2e.py``。
"""
from __future__ import annotations

import pytest

from inalpha_paper.kernel.clock import TestClock
from inalpha_paper.kernel.identifiers import InstrumentId
from inalpha_paper.kernel.msgbus import MessageBus
from inalpha_paper.model.commands import SubmitOrderCommand
from inalpha_paper.model.data import Bar
from inalpha_paper.model.orders import OrderSide
from inalpha_paper.strategies.signal_replay import SignalReplayStrategy
from inalpha_paper.strategy.base import RISK_ENGINE_ENDPOINT


def _btc() -> InstrumentId:
    return InstrumentId(symbol="BTC/USDT", venue="binance")


def _bar(close: float, ts_ms: int) -> Bar:
    """造一根 bar；ts_ms 自动转 ns（Bar.ts_event 是 ns）。"""
    return Bar(
        instrument_id=_btc(),
        timeframe="1h",
        open=close,
        high=close,
        low=close,
        close=close,
        volume=1.0,
        ts_event=ts_ms * 1_000_000,
        ts_init=ts_ms * 1_000_000,
    )


def _build(signals: list[dict]) -> tuple[SignalReplayStrategy, list[SubmitOrderCommand]]:
    clock = TestClock(0)
    bus = MessageBus()
    captured: list[SubmitOrderCommand] = []
    bus.register_endpoint(
        RISK_ENGINE_ENDPOINT,
        lambda m: captured.append(m) if isinstance(m, SubmitOrderCommand) else None,
    )
    strat = SignalReplayStrategy(
        name="t",
        clock=clock,
        msgbus=bus,
        instrument_id=_btc(),
        timeframe="1h",
        signals=signals,
    )
    strat.on_start()
    return strat, captured


# ──────────────────────────────────────────────────────────────────────
# 构造时校验
# ──────────────────────────────────────────────────────────────────────


def test_empty_signals_ok() -> None:
    strat, captured = _build([])
    assert strat.initial_signal_count == 0
    for i in range(5):
        strat.on_bar(_bar(100, ts_ms=i * 1000))
    assert captured == []


def test_signals_none_ok() -> None:
    """signals=None 不抛，等价空。"""
    clock = TestClock(0)
    bus = MessageBus()
    strat = SignalReplayStrategy("t", clock, bus, _btc(), signals=None)
    assert strat.initial_signal_count == 0


def test_missing_ts_field_raises() -> None:
    with pytest.raises(ValueError, match="ts"):
        _build([{"side": "BUY", "qty": 0.01}])


def test_missing_side_field_raises() -> None:
    with pytest.raises(ValueError, match="side"):
        _build([{"ts": 1000, "qty": 0.01}])


def test_invalid_side_raises() -> None:
    with pytest.raises(ValueError, match="BUY.*SELL"):
        _build([{"ts": 1000, "side": "HOLD", "qty": 0.01}])


def test_non_positive_qty_raises() -> None:
    with pytest.raises(ValueError, match="qty 必须 > 0"):
        _build([{"ts": 1000, "side": "BUY", "qty": 0.0}])
    with pytest.raises(ValueError, match="qty 必须 > 0"):
        _build([{"ts": 1000, "side": "BUY", "qty": -1}])


def test_non_dict_signal_raises() -> None:
    with pytest.raises(ValueError, match="必须是 dict"):
        _build([["ts", 1000]])  # type: ignore[list-item]


# ──────────────────────────────────────────────────────────────────────
# 触发时机
# ──────────────────────────────────────────────────────────────────────


def test_signal_ts_before_all_bars_fires_on_first_bar() -> None:
    """signal.ts=0 → 第一根 bar (ts=1000ms) 触发。"""
    strat, captured = _build([{"ts": 0, "side": "BUY", "qty": 0.01}])
    strat.on_bar(_bar(100, ts_ms=1000))
    assert len(captured) == 1
    assert captured[0].order.side == OrderSide.BUY
    assert captured[0].order.quantity == 0.01
    assert strat.replayed_count == 1


def test_signal_ts_after_all_bars_never_fires() -> None:
    strat, captured = _build([{"ts": 10_000_000, "side": "BUY", "qty": 0.01}])
    for i in range(5):
        strat.on_bar(_bar(100, ts_ms=i * 1000))
    assert captured == []
    assert strat.replayed_count == 0


def test_signals_distributed_across_bars() -> None:
    """3 个 signal 分布在不同 bar 上，按 ts 顺序触发。"""
    sigs = [
        {"ts": 500, "side": "BUY", "qty": 0.01},
        {"ts": 2500, "side": "SELL", "qty": 0.02},
        {"ts": 4500, "side": "BUY", "qty": 0.03},
    ]
    strat, captured = _build(sigs)
    for i in range(6):
        strat.on_bar(_bar(100, ts_ms=i * 1000))
    assert len(captured) == 3
    assert [c.order.side for c in captured] == [OrderSide.BUY, OrderSide.SELL, OrderSide.BUY]
    assert [c.order.quantity for c in captured] == [0.01, 0.02, 0.03]


def test_multiple_signals_same_bar_all_fire() -> None:
    """同一 bar 内多个 signal 全部触发，按 ts 顺序。"""
    sigs = [
        {"ts": 100, "side": "BUY", "qty": 0.01},
        {"ts": 200, "side": "BUY", "qty": 0.02},
        {"ts": 300, "side": "SELL", "qty": 0.03},
    ]
    strat, captured = _build(sigs)
    strat.on_bar(_bar(100, ts_ms=1000))
    assert len(captured) == 3
    assert [c.order.quantity for c in captured] == [0.01, 0.02, 0.03]


def test_unsorted_signals_auto_sorted() -> None:
    """signals 给乱序，内部 sort by ts。"""
    sigs = [
        {"ts": 3000, "side": "SELL", "qty": 0.02},
        {"ts": 1000, "side": "BUY", "qty": 0.01},
    ]
    strat, captured = _build(sigs)
    for i in range(5):
        strat.on_bar(_bar(100, ts_ms=i * 1000))
    assert [c.order.side for c in captured] == [OrderSide.BUY, OrderSide.SELL]


def test_ignores_unsubscribed_instrument() -> None:
    """订阅 BTC，喂 ETH bar → 不处理。"""
    eth = InstrumentId(symbol="ETH/USDT", venue="binance")
    strat, captured = _build([{"ts": 0, "side": "BUY", "qty": 0.01}])
    eth_bar = Bar(
        instrument_id=eth, timeframe="1h",
        open=100, high=100, low=100, close=100, volume=1.0,
        ts_event=1000 * 1_000_000, ts_init=1000 * 1_000_000,
    )
    strat.on_bar(eth_bar)
    assert captured == []
    # BTC bar 才触发
    strat.on_bar(_bar(100, ts_ms=1000))
    assert len(captured) == 1


def test_ignores_other_timeframe() -> None:
    strat, captured = _build([{"ts": 0, "side": "BUY", "qty": 0.01}])
    other_tf = Bar(
        instrument_id=_btc(), timeframe="5m",
        open=100, high=100, low=100, close=100, volume=1.0,
        ts_event=1000 * 1_000_000, ts_init=1000 * 1_000_000,
    )
    strat.on_bar(other_tf)
    assert captured == []


def test_string_ts_qty_coerced_to_int_float() -> None:
    """params 从 JSON 来时可能是字符串/数值混合 —— 构造时强制转。"""
    strat, captured = _build([{"ts": "1500", "side": "buy", "qty": "0.05"}])
    strat.on_bar(_bar(100, ts_ms=2000))
    assert len(captured) == 1
    assert captured[0].order.side == OrderSide.BUY
    assert captured[0].order.quantity == 0.05
