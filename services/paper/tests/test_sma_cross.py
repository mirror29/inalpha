"""``SMACrossStrategy`` 信号逻辑单测（不跑完整 engine，只验交叉判定）。"""
from __future__ import annotations

import pytest

from quant_lab_paper.kernel.clock import TestClock
from quant_lab_paper.kernel.identifiers import InstrumentId
from quant_lab_paper.kernel.msgbus import MessageBus
from quant_lab_paper.model.commands import SubmitOrderCommand
from quant_lab_paper.model.data import Bar
from quant_lab_paper.strategies.sma_cross import SMACrossStrategy
from quant_lab_paper.strategy.base import RISK_ENGINE_ENDPOINT


def _btc() -> InstrumentId:
    return InstrumentId(symbol="BTC/USDT", venue="binance")


def _bar(close: float, ts: int) -> Bar:
    return Bar(
        instrument_id=_btc(),
        timeframe="1h",
        open=close,
        high=close,
        low=close,
        close=close,
        volume=1.0,
        ts_event=ts,
        ts_init=ts,
    )


def _build_strat(fast: int = 3, slow: int = 5) -> tuple[SMACrossStrategy, list[SubmitOrderCommand]]:
    clock = TestClock(0)
    bus = MessageBus()
    captured: list[SubmitOrderCommand] = []
    bus.register_endpoint(
        RISK_ENGINE_ENDPOINT,
        lambda m: captured.append(m) if isinstance(m, SubmitOrderCommand) else None,
    )
    strat = SMACrossStrategy(
        name="t",
        clock=clock,
        msgbus=bus,
        instrument_id=_btc(),
        timeframe="1h",
        fast_period=fast,
        slow_period=slow,
    )
    strat.on_start()
    return strat, captured


def test_construction_rejects_fast_geq_slow() -> None:
    clock = TestClock(0)
    bus = MessageBus()
    with pytest.raises(ValueError, match="must be < slow_period"):
        SMACrossStrategy("t", clock, bus, _btc(), fast_period=5, slow_period=5)


def test_no_signal_during_warmup() -> None:
    strat, captured = _build_strat(fast=3, slow=5)
    for i, price in enumerate([100, 101, 102, 103]):  # 不够 slow=5 周期
        strat.on_bar(_bar(price, ts=i * 1000))
    assert captured == []
    assert strat.signal_count == 0


def test_golden_cross_triggers_buy() -> None:
    strat, captured = _build_strat(fast=3, slow=5)
    # 一开始平稳，让两根均线对齐
    # 然后快速拉升，制造金叉
    prices = [100, 100, 100, 100, 100, 95, 90, 95, 105, 115, 125]
    for i, p in enumerate(prices):
        strat.on_bar(_bar(p, ts=i * 1000))

    # 至少应有一次 BUY 信号
    assert strat.signal_count >= 1
    assert any(c.order.side.value == "BUY" for c in captured)


def test_death_cross_triggers_sell_after_buy() -> None:
    strat, captured = _build_strat(fast=3, slow=5)
    # 模拟 PositionOpened 让 strategy 进入 long 状态（绕过完整链路）
    from quant_lab_paper.model.events import PositionOpened

    # 先金叉买入
    prices_up = [100, 100, 100, 100, 100, 95, 90, 95, 105, 115, 125]
    for i, p in enumerate(prices_up):
        strat.on_bar(_bar(p, ts=i * 1000))

    # 注入 PositionOpened 让 strategy 知道 is_long
    bus_pos_topic = f"events.position.{strat.name}"
    strat.msgbus.publish(
        bus_pos_topic,
        PositionOpened(
            instrument_id=_btc(),
            strategy_id=strat.strategy_id,
            quantity=0.01,
            avg_open_price=125.0,
            realized_pnl=0.0,
            generation=2,
            ts_event=11_000,
            ts_init=11_000,
        ),
    )
    assert strat._is_long is True

    captured.clear()
    # 然后死叉
    prices_down = [120, 110, 100, 90, 80, 70, 60]
    for i, p in enumerate(prices_down):
        strat.on_bar(_bar(p, ts=(20 + i) * 1000))

    assert any(c.order.side.value == "SELL" for c in captured)


def test_no_repeat_buy_when_already_long() -> None:
    strat, captured = _build_strat(fast=3, slow=5)
    # 模拟已经持有多头
    from quant_lab_paper.model.events import PositionOpened

    # 先发一个 PositionOpened 让 strategy 进入 long 状态
    strat.msgbus.publish(
        f"events.position.{strat.name}",
        PositionOpened(
            instrument_id=_btc(),
            strategy_id=strat.strategy_id,
            quantity=0.01,
            avg_open_price=100.0,
            realized_pnl=0.0,
            generation=2,
            ts_event=0,
            ts_init=0,
        ),
    )

    # 跑一段 prices 触发金叉（如果有 is_long 校验，应该不会再发 BUY）
    prices = [100, 100, 100, 100, 100, 95, 90, 95, 105, 115, 125]
    for i, p in enumerate(prices):
        strat.on_bar(_bar(p, ts=i * 1000))

    # 已 long → 金叉不该再发 BUY
    buy_signals = [c for c in captured if c.order.side.value == "BUY"]
    assert len(buy_signals) == 0
