"""``detect_close`` 纯函数（ADR-0007 Slice 2）。

覆盖 4 种 fill 后果 + side 映射 + P&L 计算 + exit_reason 透传。
"""
from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

from inalpha_paper.engine.close_detector import (
    ClosedTradeStaging,
    detect_close,
)
from inalpha_paper.kernel.identifiers import (
    ClientOrderId,
    InstrumentId,
    StrategyId,
    VenueOrderId,
)
from inalpha_paper.model.events import OrderFilled
from inalpha_paper.model.orders import OrderSide
from inalpha_paper.model.positions import Position


def _btc() -> InstrumentId:
    return InstrumentId(symbol="BTC/USDT", venue="binance")


def _fill(
    side: OrderSide = OrderSide.SELL,
    *,
    fill_quantity: float = 1.0,
    fill_price: float = 110.0,
    ts_event: int = 1_700_000_000_000_000_000,
    client_order_id: str = "c-close",
) -> OrderFilled:
    return OrderFilled(
        client_order_id=ClientOrderId(client_order_id),
        strategy_id=StrategyId("test"),
        ts_event=ts_event,
        ts_init=ts_event,
        venue_order_id=VenueOrderId("v-1"),
        instrument_id=_btc(),
        side=side,
        fill_quantity=fill_quantity,
        fill_price=fill_price,
        trade_id="t-1",
        is_last_fill=True,
    )


def _long_position(qty: float = 1.0, avg: float = 100.0) -> Position:
    pos = Position(instrument_id=_btc())
    pos.apply_fill(OrderSide.BUY, qty, avg, ts=1_600_000_000_000_000_000)
    pos.open_order_id = "c-open"
    return pos


def _short_position(qty: float = 1.0, avg: float = 100.0) -> Position:
    pos = Position(instrument_id=_btc())
    pos.apply_fill(OrderSide.SELL, qty, avg, ts=1_600_000_000_000_000_000)
    pos.open_order_id = "c-open-short"
    return pos


# ─── 4 种 fill 后果 ───


def test_flat_position_returns_none() -> None:
    """空仓 → fill 是开仓 → 不返 close。"""
    pos = Position(instrument_id=_btc())  # FLAT
    result = detect_close(pos, _fill(OrderSide.BUY), account_id=uuid4())
    assert result is None


def test_same_direction_add_returns_none() -> None:
    """long + BUY → 加仓 → 不返 close。"""
    pos = _long_position()
    result = detect_close(pos, _fill(OrderSide.BUY), account_id=uuid4())
    assert result is None

    pos_s = _short_position()
    result = detect_close(pos_s, _fill(OrderSide.SELL), account_id=uuid4())
    assert result is None


def test_long_full_close() -> None:
    """long 1 + SELL 1 → 完全平仓，pnl_abs = (110 - 100) × 1 = +10。"""
    pos = _long_position(qty=1.0, avg=100.0)
    account_id = uuid4()
    result = detect_close(
        pos, _fill(OrderSide.SELL, fill_quantity=1.0, fill_price=110.0),
        account_id=account_id,
    )
    assert result is not None
    assert isinstance(result, ClosedTradeStaging)
    assert result.side == "long"
    assert result.quantity == Decimal("1.0")
    assert result.close_profit_abs == 10.0
    assert result.close_profit_pct == 0.1  # 10 / (100 * 1)
    assert result.open_order_id == "c-open"
    assert result.close_order_id == "c-close"
    assert result.venue == "binance"
    assert result.symbol == "BTC/USDT"
    assert result.exit_reason == "signal"  # 默认


def test_short_full_close() -> None:
    """short 1 + BUY 1 → 平空，pnl_abs = (100 - 90) × 1 = +10（low buy back）。"""
    pos = _short_position(qty=1.0, avg=100.0)
    result = detect_close(
        pos, _fill(OrderSide.BUY, fill_quantity=1.0, fill_price=90.0),
        account_id=uuid4(),
    )
    assert result is not None
    assert result.side == "short"
    assert result.close_profit_abs == 10.0


def test_partial_close_only_closed_part_returned() -> None:
    """long 3 + SELL 1 → 减仓 1（剩 2 持仓）。closed_qty=1，pnl 只算这 1。"""
    pos = _long_position(qty=3.0, avg=100.0)
    result = detect_close(
        pos, _fill(OrderSide.SELL, fill_quantity=1.0, fill_price=110.0),
        account_id=uuid4(),
    )
    assert result is not None
    assert result.quantity == Decimal("1.0")
    assert result.close_profit_abs == 10.0  # (110 - 100) × 1


def test_cross_zero_reverse_only_close_part() -> None:
    """long 1 + SELL 3 → 平 1 + 反向开 short 2。本函数只返平的 1。"""
    pos = _long_position(qty=1.0, avg=100.0)
    result = detect_close(
        pos, _fill(OrderSide.SELL, fill_quantity=3.0, fill_price=110.0),
        account_id=uuid4(),
    )
    assert result is not None
    # 只平 1 个（剩下的 2 是新 short 仓，不在 close trade 范围）
    assert result.quantity == Decimal("1.0")
    assert result.close_profit_abs == 10.0
    assert result.side == "long"  # 平仓**前**方向


# ─── P&L 计算（负值）───


def test_long_loss() -> None:
    """long 1 + SELL 1 @ 90 → 亏 10。"""
    pos = _long_position(qty=1.0, avg=100.0)
    result = detect_close(
        pos, _fill(OrderSide.SELL, fill_quantity=1.0, fill_price=90.0),
        account_id=uuid4(),
    )
    assert result is not None
    assert result.close_profit_abs == -10.0
    assert result.close_profit_pct == -0.1


def test_short_loss() -> None:
    """short 1 + BUY 1 @ 110 → 亏 10（high buy back）。"""
    pos = _short_position(qty=1.0, avg=100.0)
    result = detect_close(
        pos, _fill(OrderSide.BUY, fill_quantity=1.0, fill_price=110.0),
        account_id=uuid4(),
    )
    assert result is not None
    assert result.close_profit_abs == -10.0


# ─── exit_reason 透传 ───


def test_order_tag_propagates_as_exit_reason() -> None:
    pos = _long_position()
    result = detect_close(
        pos, _fill(OrderSide.SELL),
        account_id=uuid4(),
        order_tag="stop_loss",
    )
    assert result is not None
    assert result.exit_reason == "stop_loss"


def test_no_tag_defaults_signal() -> None:
    pos = _long_position()
    result = detect_close(
        pos, _fill(OrderSide.SELL),
        account_id=uuid4(),
        order_tag=None,
    )
    assert result is not None
    assert result.exit_reason == "signal"


# ─── ts 转换 + open_order_id 透传 ───


def test_open_order_id_from_position() -> None:
    """open_order_id 来自 prev_position.open_order_id；close_order_id 来自 fill."""
    pos = _long_position()
    pos.open_order_id = "my-open-order-42"
    result = detect_close(
        pos, _fill(OrderSide.SELL, client_order_id="my-close-order-99"),
        account_id=uuid4(),
    )
    assert result is not None
    assert result.open_order_id == "my-open-order-42"
    assert result.close_order_id == "my-close-order-99"


def test_open_order_id_none_propagates() -> None:
    """prev_position.open_order_id = None 时透传 None。"""
    pos = _long_position()
    pos.open_order_id = None
    result = detect_close(pos, _fill(OrderSide.SELL), account_id=uuid4())
    assert result is not None
    assert result.open_order_id is None


def test_ts_converted_to_tz_aware_utc() -> None:
    """epoch ns → tz-aware UTC datetime。"""
    pos = _long_position()
    pos.ts_opened = 1_700_000_000_000_000_000  # 2023-11-14 22:13:20 UTC
    fill_ts = 1_700_003_600_000_000_000  # 1h later

    result = detect_close(
        pos, _fill(OrderSide.SELL, ts_event=fill_ts),
        account_id=uuid4(),
    )
    assert result is not None
    assert result.open_ts.tzinfo is not None
    assert result.close_ts.tzinfo is not None
    assert (result.close_ts - result.open_ts).total_seconds() == 3600.0


# ─── frozen 不可变 ───


def test_staging_is_frozen() -> None:
    pos = _long_position()
    result = detect_close(pos, _fill(OrderSide.SELL), account_id=uuid4())
    assert result is not None
    import pytest

    with pytest.raises(AttributeError):
        result.exit_reason = "manual"  # type: ignore[misc]
