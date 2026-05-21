"""``SimulatedExchange`` 撮合逻辑单测。"""
from __future__ import annotations

from typing import Any

from quant_lab_paper.execution.exchange import SimulatedExchange
from quant_lab_paper.kernel.clock import TestClock
from quant_lab_paper.kernel.identifiers import ClientOrderId, InstrumentId, StrategyId
from quant_lab_paper.kernel.msgbus import MessageBus
from quant_lab_paper.model.data import Bar
from quant_lab_paper.model.orders import Order, OrderSide, OrderType


def _btc() -> InstrumentId:
    return InstrumentId(symbol="BTC/USDT", venue="binance")


def _make_bar(open: float, high: float, low: float, close: float, ts: int = 1000) -> Bar:
    return Bar(
        instrument_id=_btc(),
        timeframe="1h",
        open=open,
        high=high,
        low=low,
        close=close,
        volume=1000.0,
        ts_event=ts,
        ts_init=ts,
    )


def _market_buy(qty: float = 1.0) -> Order:
    return Order(
        client_order_id=ClientOrderId("c-1"),
        instrument_id=_btc(),
        side=OrderSide.BUY,
        type=OrderType.MARKET,
        quantity=qty,
    )


def _limit_buy(qty: float, price: float) -> Order:
    return Order(
        client_order_id=ClientOrderId(f"c-l-{price}"),
        instrument_id=_btc(),
        side=OrderSide.BUY,
        type=OrderType.LIMIT,
        quantity=qty,
        price=price,
    )


def _limit_sell(qty: float, price: float) -> Order:
    return Order(
        client_order_id=ClientOrderId(f"c-ls-{price}"),
        instrument_id=_btc(),
        side=OrderSide.SELL,
        type=OrderType.LIMIT,
        quantity=qty,
        price=price,
    )


def _capture(bus: MessageBus, topic: str) -> list[dict[str, Any]]:
    captured: list[dict[str, Any]] = []
    bus.subscribe(topic, lambda m: captured.append(m))  # type: ignore[arg-type]
    return captured


# ─── send_order: accepted / rejected ───


def test_send_market_order_publishes_accepted() -> None:
    bus = MessageBus()
    clock = TestClock(1_000_000_000)
    ex = SimulatedExchange(bus, clock)

    accepted = _capture(bus, "internal.venue.accepted")
    ex.send_order(_market_buy(), StrategyId("s1"))

    assert len(accepted) == 1
    assert accepted[0]["client_order_id"] == "c-1"
    assert accepted[0]["strategy_id"] == "s1"
    assert ex.pending_count() == 1


def test_unsupported_order_type_is_rejected() -> None:
    bus = MessageBus()
    clock = TestClock(0)
    ex = SimulatedExchange(bus, clock)

    rejected = _capture(bus, "internal.venue.rejected")

    stop = Order(
        client_order_id=ClientOrderId("stop-1"),
        instrument_id=_btc(),
        side=OrderSide.BUY,
        type=OrderType.STOP_MARKET,
        quantity=1.0,
    )
    ex.send_order(stop, StrategyId("s1"))

    assert len(rejected) == 1
    assert "STOP_MARKET" in rejected[0]["reason"]
    assert ex.pending_count() == 0


# ─── 市价单撮合 ───


def test_market_buy_fills_at_open() -> None:
    bus = MessageBus()
    clock = TestClock(0)
    ex = SimulatedExchange(bus, clock)
    fills = _capture(bus, "internal.venue.filled")

    ex.send_order(_market_buy(qty=2.0), StrategyId("s1"))
    bar = _make_bar(open=100.0, high=105.0, low=99.0, close=102.0)
    n = ex.process_bar(bar)

    assert n == 1
    assert len(fills) == 1
    assert fills[0]["fill_price"] == 100.0  # bar.open
    assert fills[0]["fill_qty"] == 2.0
    assert ex.pending_count() == 0  # 已撮合


# ─── 限价买撮合 ───


def test_limit_buy_filled_when_low_touches() -> None:
    bus = MessageBus()
    clock = TestClock(0)
    ex = SimulatedExchange(bus, clock)
    fills = _capture(bus, "internal.venue.filled")

    # 限价 99，bar 区间 [98, 105]，应该成交
    ex.send_order(_limit_buy(qty=1.0, price=99.0), StrategyId("s1"))
    bar = _make_bar(open=100.0, high=105.0, low=98.0, close=102.0)
    ex.process_bar(bar)

    assert len(fills) == 1
    # 成交价 = min(限价, open) = min(99, 100) = 99
    assert fills[0]["fill_price"] == 99.0


def test_limit_buy_filled_at_open_when_open_is_lower() -> None:
    bus = MessageBus()
    clock = TestClock(0)
    ex = SimulatedExchange(bus, clock)
    fills = _capture(bus, "internal.venue.filled")

    # 限价 110，open=100 < 限价，成交价 = open = 100
    ex.send_order(_limit_buy(qty=1.0, price=110.0), StrategyId("s1"))
    bar = _make_bar(open=100.0, high=105.0, low=98.0, close=102.0)
    ex.process_bar(bar)

    assert len(fills) == 1
    assert fills[0]["fill_price"] == 100.0


def test_limit_buy_not_filled_when_low_above_limit() -> None:
    bus = MessageBus()
    clock = TestClock(0)
    ex = SimulatedExchange(bus, clock)
    fills = _capture(bus, "internal.venue.filled")

    # 限价 50，bar low=98 > 50，没触发
    ex.send_order(_limit_buy(qty=1.0, price=50.0), StrategyId("s1"))
    bar = _make_bar(open=100.0, high=105.0, low=98.0, close=102.0)
    ex.process_bar(bar)

    assert len(fills) == 0
    assert ex.pending_count() == 1  # 还在 pending


# ─── 限价卖撮合 ───


def test_limit_sell_filled_when_high_touches() -> None:
    bus = MessageBus()
    clock = TestClock(0)
    ex = SimulatedExchange(bus, clock)
    fills = _capture(bus, "internal.venue.filled")

    # 限价 110，bar high=115，应该成交
    ex.send_order(_limit_sell(qty=1.0, price=110.0), StrategyId("s1"))
    bar = _make_bar(open=100.0, high=115.0, low=98.0, close=102.0)
    ex.process_bar(bar)

    assert len(fills) == 1
    # 成交价 = max(限价, open) = max(110, 100) = 110
    assert fills[0]["fill_price"] == 110.0


def test_limit_sell_not_filled_when_high_below_limit() -> None:
    bus = MessageBus()
    clock = TestClock(0)
    ex = SimulatedExchange(bus, clock)
    fills = _capture(bus, "internal.venue.filled")

    ex.send_order(_limit_sell(qty=1.0, price=200.0), StrategyId("s1"))
    bar = _make_bar(open=100.0, high=105.0, low=98.0, close=102.0)
    ex.process_bar(bar)

    assert len(fills) == 0


# ─── 多订单 ───


def test_multiple_pending_orders_processed_in_one_bar() -> None:
    bus = MessageBus()
    clock = TestClock(0)
    ex = SimulatedExchange(bus, clock)
    fills = _capture(bus, "internal.venue.filled")

    ex.send_order(_market_buy(qty=1.0), StrategyId("s1"))
    ex.send_order(_limit_buy(qty=2.0, price=99.0), StrategyId("s1"))

    bar = _make_bar(open=100.0, high=105.0, low=98.0, close=102.0)
    n = ex.process_bar(bar)

    assert n == 2
    assert len(fills) == 2
    assert ex.pending_count() == 0


def test_order_for_other_instrument_not_touched() -> None:
    bus = MessageBus()
    clock = TestClock(0)
    ex = SimulatedExchange(bus, clock)
    fills = _capture(bus, "internal.venue.filled")

    eth_order = Order(
        client_order_id=ClientOrderId("c-eth"),
        instrument_id=InstrumentId(symbol="ETH/USDT", venue="binance"),
        side=OrderSide.BUY,
        type=OrderType.MARKET,
        quantity=1.0,
    )
    ex.send_order(eth_order, StrategyId("s1"))
    # 喂 BTC bar
    bar = _make_bar(open=100.0, high=105.0, low=98.0, close=102.0)
    n = ex.process_bar(bar)

    assert n == 0
    assert len(fills) == 0
    assert ex.pending_count() == 1


# ─── cancel ───


def test_cancel_pending_order() -> None:
    bus = MessageBus()
    clock = TestClock(0)
    ex = SimulatedExchange(bus, clock)
    canceled = _capture(bus, "internal.venue.canceled")

    ex.send_order(_limit_buy(qty=1.0, price=50.0), StrategyId("s1"))
    ex.cancel_order(ClientOrderId("c-l-50.0"))

    assert len(canceled) == 1
    assert ex.pending_count() == 0


def test_cancel_unknown_order_is_noop() -> None:
    bus = MessageBus()
    clock = TestClock(0)
    ex = SimulatedExchange(bus, clock)
    canceled = _capture(bus, "internal.venue.canceled")

    ex.cancel_order(ClientOrderId("nope"))  # 不应抛
    assert len(canceled) == 0
