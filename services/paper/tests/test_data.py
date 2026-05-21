"""数据 dataclass 的不可变性 + ``data_epoch`` 字段。"""
from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from inalpha_paper.kernel.identifiers import InstrumentId
from inalpha_paper.model.data import Bar, QuoteTick, TradeTick


def _btc() -> InstrumentId:
    return InstrumentId(symbol="BTC/USDT", venue="binance")


def test_quote_tick_is_immutable() -> None:
    tick = QuoteTick(
        instrument_id=_btc(),
        bid_price=100.0,
        ask_price=100.1,
        bid_size=1.5,
        ask_size=2.0,
        ts_event=1_000_000_000,
        ts_init=1_000_000_001,
    )
    with pytest.raises(FrozenInstanceError):
        tick.bid_price = 200.0  # type: ignore[misc]


def test_quote_tick_default_data_epoch() -> None:
    tick = QuoteTick(
        instrument_id=_btc(),
        bid_price=100.0,
        ask_price=100.1,
        bid_size=1.0,
        ask_size=1.0,
        ts_event=0,
        ts_init=0,
    )
    assert tick.data_epoch == 1
    assert tick.is_stale_after_reconnect is False


def test_quote_tick_with_stale_flag() -> None:
    tick = QuoteTick(
        instrument_id=_btc(),
        bid_price=100.0,
        ask_price=100.1,
        bid_size=1.0,
        ask_size=1.0,
        ts_event=0,
        ts_init=0,
        data_epoch=5,
        is_stale_after_reconnect=True,
    )
    assert tick.data_epoch == 5
    assert tick.is_stale_after_reconnect is True


def test_trade_tick_has_aggressor_side() -> None:
    tick = TradeTick(
        instrument_id=_btc(),
        price=100.5,
        size=0.5,
        aggressor_side="BUY",
        trade_id="t1",
        ts_event=0,
        ts_init=0,
    )
    assert tick.aggressor_side == "BUY"


def test_bar_with_timeframe() -> None:
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
    assert bar.timeframe == "1h"
    assert bar.data_epoch == 1


def test_instrument_id_str() -> None:
    assert str(_btc()) == "BTC/USDT@binance"
