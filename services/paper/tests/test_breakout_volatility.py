"""``DonchianBreakoutStrategy`` / ``ATRChannelStrategy`` 信号逻辑单测（docs/miro/11 M4）。

不跑完整 engine，只验通道突破判定 + 无 lookahead（用历史通道决策）。
"""
from __future__ import annotations

from inalpha_paper.kernel.clock import TestClock
from inalpha_paper.kernel.identifiers import InstrumentId
from inalpha_paper.kernel.msgbus import MessageBus
from inalpha_paper.model.commands import SubmitOrderCommand
from inalpha_paper.model.data import Bar
from inalpha_paper.model.events import PositionOpened
from inalpha_paper.strategies.atr_channel import ATRChannelStrategy
from inalpha_paper.strategies.donchian_breakout import DonchianBreakoutStrategy
from inalpha_paper.strategy.base import RISK_ENGINE_ENDPOINT


def _btc() -> InstrumentId:
    return InstrumentId(symbol="BTC/USDT", venue="binance")


def _bar(high: float, low: float, close: float, ts: int) -> Bar:
    return Bar(
        instrument_id=_btc(),
        timeframe="1h",
        open=close,
        high=high,
        low=low,
        close=close,
        volume=1.0,
        ts_event=ts,
        ts_init=ts,
    )


def _capture(strat) -> list[SubmitOrderCommand]:  # type: ignore[no-untyped-def]
    captured: list[SubmitOrderCommand] = []
    strat.msgbus.register_endpoint(
        RISK_ENGINE_ENDPOINT,
        lambda m: captured.append(m) if isinstance(m, SubmitOrderCommand) else None,
    )
    return captured


def _go_long(strat, qty: float = 0.01, price: float = 100.0) -> None:  # type: ignore[no-untyped-def]
    strat.msgbus.publish(
        f"events.position.{strat.name}",
        PositionOpened(
            instrument_id=_btc(),
            strategy_id=strat.strategy_id,
            quantity=qty,
            avg_open_price=price,
            realized_pnl=0.0,
            generation=2,
            ts_event=0,
            ts_init=0,
        ),
    )


# ── Donchian breakout ────────────────────────────────────────────────


def test_donchian_breaks_out_above_channel() -> None:
    strat = DonchianBreakoutStrategy(
        "t", TestClock(0), MessageBus(), _btc(), channel_period=5, exit_period=3
    )
    captured = _capture(strat)
    strat.on_start()
    # 前 5 根盘整在 100±1（建立通道 high≈101），第 6 根冲到 110 → 突破买
    flat = [(101, 99, 100)] * 5
    for i, (h, lo, c) in enumerate(flat):
        strat.on_bar(_bar(h, lo, c, ts=i * 1000))
    assert captured == []  # 预热 / 盘整无信号
    strat.on_bar(_bar(112, 108, 110, ts=6000))
    assert any(c.order.side.value == "BUY" for c in captured)
    assert strat.signal_count >= 1


def test_donchian_exits_below_lower_channel() -> None:
    strat = DonchianBreakoutStrategy(
        "t", TestClock(0), MessageBus(), _btc(), channel_period=5, exit_period=3
    )
    captured = _capture(strat)
    strat.on_start()
    _go_long(strat)
    for i, (h, lo, c) in enumerate([(101, 99, 100)] * 5):
        strat.on_bar(_bar(h, lo, c, ts=i * 1000))
    captured.clear()
    # 跌破前 3 根最低（≈99）→ 平仓
    strat.on_bar(_bar(96, 90, 92, ts=6000))
    assert any(c.order.side.value == "SELL" for c in captured)


def test_donchian_no_signal_during_warmup() -> None:
    strat = DonchianBreakoutStrategy(
        "t", TestClock(0), MessageBus(), _btc(), channel_period=5, exit_period=3
    )
    captured = _capture(strat)
    strat.on_start()
    for i, (h, lo, c) in enumerate([(101, 99, 100)] * 3):  # < channel_period
        strat.on_bar(_bar(h, lo, c, ts=i * 1000))
    assert captured == []


# ── ATR channel ──────────────────────────────────────────────────────


def test_atr_channel_breaks_out_above_upper() -> None:
    strat = ATRChannelStrategy(
        "t", TestClock(0), MessageBus(), _btc(), period=5, atr_mult=1.5
    )
    captured = _capture(strat)
    strat.on_start()
    # 盘整建立中轨≈100 + 小 ATR，然后大幅冲高破上轨
    for i in range(6):
        strat.on_bar(_bar(101, 99, 100, ts=i * 1000))
    strat.on_bar(_bar(130, 120, 128, ts=7000))
    assert any(c.order.side.value == "BUY" for c in captured)


def test_atr_channel_exits_below_mid() -> None:
    strat = ATRChannelStrategy(
        "t", TestClock(0), MessageBus(), _btc(), period=5, atr_mult=1.5
    )
    captured = _capture(strat)
    strat.on_start()
    _go_long(strat)
    for i in range(6):
        strat.on_bar(_bar(101, 99, 100, ts=i * 1000))
    captured.clear()
    # 跌回中轨（≈100）下方 → 平仓
    strat.on_bar(_bar(95, 90, 92, ts=7000))
    assert any(c.order.side.value == "SELL" for c in captured)
