"""``Order`` + 7 状态机 + ``apply_fill``。"""
from __future__ import annotations

import pytest

from inalpha_paper.kernel.identifiers import ClientOrderId, InstrumentId, VenueOrderId
from inalpha_paper.model.orders import Order, OrderSide, OrderStatus, OrderType


def _btc() -> InstrumentId:
    return InstrumentId(symbol="BTC/USDT", venue="binance")


def _make_market_buy(qty: float = 1.0) -> Order:
    return Order(
        client_order_id=ClientOrderId("c-1"),
        instrument_id=_btc(),
        side=OrderSide.BUY,
        type=OrderType.MARKET,
        quantity=qty,
    )


def _make_limit_buy(qty: float = 1.0, price: float = 100.0) -> Order:
    return Order(
        client_order_id=ClientOrderId("c-2"),
        instrument_id=_btc(),
        side=OrderSide.BUY,
        type=OrderType.LIMIT,
        quantity=qty,
        price=price,
    )


# ─── 构造时校验 ───


def test_market_order_must_not_have_price() -> None:
    with pytest.raises(ValueError, match="MARKET order must not specify price"):
        Order(
            client_order_id=ClientOrderId("c"),
            instrument_id=_btc(),
            side=OrderSide.BUY,
            type=OrderType.MARKET,
            quantity=1.0,
            price=100.0,
        )


def test_limit_order_requires_price() -> None:
    with pytest.raises(ValueError, match=r"LIMIT.*requires price"):
        Order(
            client_order_id=ClientOrderId("c"),
            instrument_id=_btc(),
            side=OrderSide.BUY,
            type=OrderType.LIMIT,
            quantity=1.0,
        )


def test_quantity_must_be_positive() -> None:
    with pytest.raises(ValueError, match="quantity must be positive"):
        Order(
            client_order_id=ClientOrderId("c"),
            instrument_id=_btc(),
            side=OrderSide.BUY,
            type=OrderType.MARKET,
            quantity=0.0,
        )


# ─── 状态机 ───


def test_happy_path_submit_accept_fill() -> None:
    order = _make_market_buy(qty=1.0)
    assert order.status == OrderStatus.NEW
    assert order.is_active is True

    order.mark_submitted(ts=100)
    assert order.status == OrderStatus.SUBMITTED

    order.mark_accepted(VenueOrderId("v-1"), ts=200)
    assert order.status == OrderStatus.ACCEPTED
    assert order.venue_order_id == VenueOrderId("v-1")

    order.apply_fill(fill_quantity=1.0, fill_price=100.5, ts=300)
    assert order.status == OrderStatus.FILLED
    assert order.is_terminal is True
    assert order.filled_quantity == 1.0
    assert order.avg_fill_price == 100.5


def test_partial_fill_then_full() -> None:
    order = _make_limit_buy(qty=2.0, price=100.0)
    order.mark_submitted(ts=100)
    order.mark_accepted(VenueOrderId("v"), ts=200)

    order.apply_fill(fill_quantity=0.5, fill_price=100.0, ts=300)
    assert order.status == OrderStatus.PARTIALLY_FILLED
    assert order.filled_quantity == 0.5
    assert order.avg_fill_price == 100.0

    order.apply_fill(fill_quantity=1.0, fill_price=101.0, ts=400)
    assert order.status == OrderStatus.PARTIALLY_FILLED
    # 加权平均：(0.5*100 + 1.0*101) / 1.5 = 151 / 1.5 ≈ 100.667
    assert order.avg_fill_price == pytest.approx(151.0 / 1.5)

    order.apply_fill(fill_quantity=0.5, fill_price=102.0, ts=500)
    assert order.status == OrderStatus.FILLED
    # 加权平均：(0.5*100 + 1.0*101 + 0.5*102) / 2.0 = (50+101+51) / 2 = 101.0
    assert order.avg_fill_price == pytest.approx(101.0)


def test_reject_from_submitted() -> None:
    order = _make_market_buy()
    order.mark_submitted(ts=100)
    order.mark_rejected(reason="insufficient balance", ts=200)
    assert order.status == OrderStatus.REJECTED
    assert order.reason == "insufficient balance"


def test_cancel_after_partial_fill() -> None:
    order = _make_limit_buy(qty=2.0)
    order.mark_submitted(ts=100)
    order.mark_accepted(VenueOrderId("v"), ts=200)
    order.apply_fill(0.5, 100.0, ts=300)
    order.mark_canceled(ts=400, reason="user")
    assert order.status == OrderStatus.CANCELED
    assert order.filled_quantity == 0.5  # partial 成交保留


def test_invalid_transition() -> None:
    order = _make_market_buy()
    with pytest.raises(ValueError, match="invalid transition: NEW → FILLED"):
        order._transition(OrderStatus.FILLED, ts=0)


def test_cannot_fill_in_new_status() -> None:
    order = _make_market_buy()
    with pytest.raises(ValueError, match="cannot fill order in status NEW"):
        order.apply_fill(1.0, 100.0, ts=100)


def test_overfill_rejected() -> None:
    order = _make_market_buy(qty=1.0)
    order.mark_submitted(ts=100)
    order.mark_accepted(VenueOrderId("v"), ts=200)
    with pytest.raises(ValueError, match="exceeds remaining"):
        order.apply_fill(2.0, 100.0, ts=300)


def test_remaining_quantity() -> None:
    order = _make_market_buy(qty=2.0)
    order.mark_submitted(ts=0)
    order.mark_accepted(VenueOrderId("v"), ts=0)
    assert order.remaining_quantity == 2.0
    order.apply_fill(0.7, 100.0, ts=1)
    assert order.remaining_quantity == pytest.approx(1.3)
