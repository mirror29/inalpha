"""``Actor`` + ``Strategy`` 端到端：数据订阅 → 回调 → 下单 → endpoint。"""
from __future__ import annotations

from typing import Any

import pytest

from inalpha_paper.kernel.clock import TestClock
from inalpha_paper.kernel.identifiers import ClientOrderId, InstrumentId, VenueOrderId
from inalpha_paper.kernel.msgbus import MessageBus
from inalpha_paper.model.commands import SubmitOrderCommand
from inalpha_paper.model.data import Bar, QuoteTick
from inalpha_paper.model.events import OrderFilled, PositionOpened
from inalpha_paper.model.orders import Order, OrderSide, OrderType
from inalpha_paper.strategy.actor import Actor
from inalpha_paper.strategy.base import RISK_ENGINE_ENDPOINT, Strategy


def _btc() -> InstrumentId:
    return InstrumentId(symbol="BTC/USDT", venue="binance")


# ─── Actor 数据订阅 ───


class _CapturingActor(Actor):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.quote_ticks: list[QuoteTick] = []
        self.bars: list[Bar] = []

    def on_quote_tick(self, tick: QuoteTick) -> None:
        self.quote_ticks.append(tick)

    def on_bar(self, bar: Bar) -> None:
        self.bars.append(bar)


def test_actor_receives_subscribed_quote_ticks() -> None:
    clock = TestClock(0)
    bus = MessageBus()
    actor = _CapturingActor("a1", clock, bus)
    actor.subscribe_quote_ticks(_btc())

    tick = QuoteTick(
        instrument_id=_btc(),
        bid_price=100.0,
        ask_price=100.1,
        bid_size=1.0,
        ask_size=1.0,
        ts_event=1000,
        ts_init=1001,
    )
    bus.publish("data.quotes.binance.BTC/USDT", tick)
    assert actor.quote_ticks == [tick]


def test_actor_does_not_receive_unsubscribed_symbol() -> None:
    clock = TestClock(0)
    bus = MessageBus()
    actor = _CapturingActor("a", clock, bus)
    actor.subscribe_quote_ticks(_btc())

    eth_tick = QuoteTick(
        instrument_id=InstrumentId(symbol="ETH/USDT", venue="binance"),
        bid_price=3000.0,
        ask_price=3000.1,
        bid_size=1.0,
        ask_size=1.0,
        ts_event=0,
        ts_init=0,
    )
    bus.publish("data.quotes.binance.ETH/USDT", eth_tick)
    assert actor.quote_ticks == []


def test_actor_subscribes_bars_with_timeframe() -> None:
    clock = TestClock(0)
    bus = MessageBus()
    actor = _CapturingActor("a", clock, bus)
    actor.subscribe_bars(_btc(), timeframe="1h")

    bar = Bar(
        instrument_id=_btc(),
        timeframe="1h",
        open=100.0,
        high=101.0,
        low=99.0,
        close=100.5,
        volume=1000.0,
        ts_event=0,
        ts_init=0,
    )
    bus.publish("data.bars.binance.BTC/USDT.1h", bar)
    assert actor.bars == [bar]


def test_actor_ignores_other_timeframes() -> None:
    clock = TestClock(0)
    bus = MessageBus()
    actor = _CapturingActor("a", clock, bus)
    actor.subscribe_bars(_btc(), timeframe="1h")

    # 5m 不应该到
    bar_5m = Bar(
        instrument_id=_btc(),
        timeframe="5m",
        open=100.0,
        high=101.0,
        low=99.0,
        close=100.5,
        volume=1000.0,
        ts_event=0,
        ts_init=0,
    )
    bus.publish("data.bars.binance.BTC/USDT.5m", bar_5m)
    assert actor.bars == []


# ─── Strategy 下单 ───


def test_strategy_submit_order_sends_to_risk_endpoint() -> None:
    clock = TestClock(1_000_000_000)
    bus = MessageBus()

    captured: list[SubmitOrderCommand] = []
    bus.register_endpoint(RISK_ENGINE_ENDPOINT, captured.append)

    strat = Strategy("my-strat", clock, bus)
    order = Order(
        client_order_id=ClientOrderId("c-1"),
        instrument_id=_btc(),
        side=OrderSide.BUY,
        type=OrderType.MARKET,
        quantity=0.05,
    )
    strat.submit_order(order)

    assert len(captured) == 1
    cmd = captured[0]
    assert isinstance(cmd, SubmitOrderCommand)
    assert cmd.order is order
    assert cmd.strategy_id == "my-strat"
    assert cmd.ts_init == 1_000_000_000


def test_strategy_submit_order_without_risk_endpoint_raises() -> None:
    """没注册 endpoint，submit_order 抛 KeyError（不静默吞）。"""
    clock = TestClock(0)
    bus = MessageBus()
    strat = Strategy("s", clock, bus)
    order = Order(
        client_order_id=ClientOrderId("c"),
        instrument_id=_btc(),
        side=OrderSide.BUY,
        type=OrderType.MARKET,
        quantity=1.0,
    )
    with pytest.raises(KeyError, match=RISK_ENGINE_ENDPOINT):
        strat.submit_order(order)


def test_modify_order_requires_at_least_one_change() -> None:
    clock = TestClock(0)
    bus = MessageBus()
    bus.register_endpoint(RISK_ENGINE_ENDPOINT, lambda _: None)
    strat = Strategy("s", clock, bus)
    with pytest.raises(ValueError, match="must specify"):
        strat.modify_order(ClientOrderId("c"))


# ─── Strategy 事件回调 ───


class _CapturingStrategy(Strategy):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fills: list[OrderFilled] = []
        self.opened: list[PositionOpened] = []

    def on_order_filled(self, event: OrderFilled) -> None:
        self.fills.append(event)

    def on_position_opened(self, event: PositionOpened) -> None:
        self.opened.append(event)


def test_strategy_receives_order_filled_event() -> None:
    clock = TestClock(0)
    bus = MessageBus()
    strat = _CapturingStrategy("s1", clock, bus)

    evt = OrderFilled(
        client_order_id=ClientOrderId("c-1"),
        strategy_id=strat.strategy_id,
        ts_event=2000,
        ts_init=2001,
        venue_order_id=VenueOrderId("v"),
        instrument_id=_btc(),
        side=OrderSide.BUY,
        fill_quantity=1.0,
        fill_price=100.5,
        trade_id="t1",
        is_last_fill=True,
    )
    bus.publish("events.order.s1", evt)
    assert strat.fills == [evt]


def test_strategy_receives_position_opened_event() -> None:
    clock = TestClock(0)
    bus = MessageBus()
    strat = _CapturingStrategy("s1", clock, bus)

    evt = PositionOpened(
        instrument_id=_btc(),
        strategy_id=strat.strategy_id,
        quantity=1.0,
        avg_open_price=100.0,
        realized_pnl=0.0,
        generation=2,
        ts_event=0,
        ts_init=0,
    )
    bus.publish("events.position.s1", evt)
    assert strat.opened == [evt]


def test_strategy_only_receives_own_strategy_events() -> None:
    clock = TestClock(0)
    bus = MessageBus()
    strat_a = _CapturingStrategy("strat-a", clock, bus)
    _CapturingStrategy("strat-b", clock, bus)  # 也订阅了

    evt_b = OrderFilled(
        client_order_id=ClientOrderId("c"),
        strategy_id="strat-b",  # type: ignore[arg-type]
        ts_event=0,
        ts_init=0,
    )
    bus.publish("events.order.strat-b", evt_b)
    # strat_a 不应收到 strat-b 的事件
    assert strat_a.fills == []
