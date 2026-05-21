"""``Position`` —— 加仓 / 减仓 / 反向开仓 / generation / from_fills reconcile。"""
from __future__ import annotations

import pytest

from inalpha_paper.kernel.identifiers import InstrumentId
from inalpha_paper.model.orders import OrderSide
from inalpha_paper.model.positions import Position, PositionSide


def _btc() -> InstrumentId:
    return InstrumentId(symbol="BTC/USDT", venue="binance")


# ─── 基础 ───


def test_new_position_is_flat() -> None:
    p = Position(instrument_id=_btc())
    assert p.is_flat is True
    assert p.side == PositionSide.FLAT
    assert p.quantity == 0.0
    assert p.generation == 1


# ─── 开仓 ───


def test_open_long_position() -> None:
    p = Position(instrument_id=_btc())
    p.apply_fill(OrderSide.BUY, fill_quantity=1.0, fill_price=100.0, ts=1000)
    assert p.side == PositionSide.LONG
    assert p.quantity == 1.0
    assert p.avg_open_price == 100.0
    assert p.generation == 2
    assert p.ts_opened == 1000


def test_open_short_position() -> None:
    p = Position(instrument_id=_btc())
    p.apply_fill(OrderSide.SELL, fill_quantity=1.0, fill_price=100.0, ts=1000)
    assert p.side == PositionSide.SHORT
    assert p.quantity == -1.0
    assert p.avg_open_price == 100.0


# ─── 加仓（加权平均） ───


def test_add_to_long() -> None:
    p = Position(instrument_id=_btc())
    p.apply_fill(OrderSide.BUY, 1.0, 100.0, ts=1)
    p.apply_fill(OrderSide.BUY, 1.0, 110.0, ts=2)
    assert p.quantity == 2.0
    assert p.avg_open_price == 105.0
    assert p.generation == 3


def test_add_to_short() -> None:
    p = Position(instrument_id=_btc())
    p.apply_fill(OrderSide.SELL, 1.0, 100.0, ts=1)
    p.apply_fill(OrderSide.SELL, 1.0, 110.0, ts=2)
    assert p.quantity == -2.0
    assert p.avg_open_price == 105.0


# ─── 减仓 / 平仓 ───


def test_partial_close_long() -> None:
    p = Position(instrument_id=_btc())
    p.apply_fill(OrderSide.BUY, 2.0, 100.0, ts=1)
    p.apply_fill(OrderSide.SELL, 1.0, 110.0, ts=2)
    assert p.quantity == 1.0
    assert p.avg_open_price == 100.0  # 减仓不动平均开仓价
    assert p.realized_pnl == pytest.approx(10.0)  # 1 * (110-100)


def test_full_close_long_back_to_flat() -> None:
    p = Position(instrument_id=_btc())
    p.apply_fill(OrderSide.BUY, 1.0, 100.0, ts=1)
    p.apply_fill(OrderSide.SELL, 1.0, 110.0, ts=2)
    assert p.is_flat is True
    assert p.avg_open_price == 0.0
    assert p.realized_pnl == pytest.approx(10.0)


def test_full_close_short() -> None:
    p = Position(instrument_id=_btc())
    p.apply_fill(OrderSide.SELL, 1.0, 100.0, ts=1)
    p.apply_fill(OrderSide.BUY, 1.0, 95.0, ts=2)  # 跌了 5 块买回，赚 5
    assert p.is_flat is True
    assert p.realized_pnl == pytest.approx(5.0)


# ─── 反向开仓（先平再开） ───


def test_flip_long_to_short() -> None:
    p = Position(instrument_id=_btc())
    p.apply_fill(OrderSide.BUY, 1.0, 100.0, ts=1)
    # 卖出 2.0，平掉 1.0 + 反向开 1.0
    p.apply_fill(OrderSide.SELL, 2.0, 110.0, ts=2)
    assert p.quantity == -1.0
    assert p.side == PositionSide.SHORT
    assert p.avg_open_price == 110.0  # 反向开仓用本次成交价
    assert p.realized_pnl == pytest.approx(10.0)  # 平多赚的


def test_flip_short_to_long() -> None:
    p = Position(instrument_id=_btc())
    p.apply_fill(OrderSide.SELL, 1.0, 100.0, ts=1)
    p.apply_fill(OrderSide.BUY, 2.0, 90.0, ts=2)  # 跌后买回 + 反向多
    assert p.quantity == 1.0
    assert p.side == PositionSide.LONG
    assert p.avg_open_price == 90.0
    assert p.realized_pnl == pytest.approx(10.0)


# ─── 浮盈 ───


def test_unrealized_pnl_long() -> None:
    p = Position(instrument_id=_btc())
    p.apply_fill(OrderSide.BUY, 1.0, 100.0, ts=1)
    assert p.unrealized_pnl(mark_price=105.0) == pytest.approx(5.0)
    assert p.unrealized_pnl(mark_price=95.0) == pytest.approx(-5.0)


def test_unrealized_pnl_short() -> None:
    p = Position(instrument_id=_btc())
    p.apply_fill(OrderSide.SELL, 1.0, 100.0, ts=1)
    assert p.unrealized_pnl(mark_price=95.0) == pytest.approx(5.0)


def test_unrealized_pnl_flat_is_zero() -> None:
    p = Position(instrument_id=_btc())
    assert p.unrealized_pnl(mark_price=100.0) == 0.0


# ─── generation（ADR-0013） ───


def test_generation_increments_per_fill() -> None:
    p = Position(instrument_id=_btc())
    assert p.generation == 1
    p.apply_fill(OrderSide.BUY, 1.0, 100.0, ts=1)
    assert p.generation == 2
    p.apply_fill(OrderSide.SELL, 0.5, 110.0, ts=2)
    assert p.generation == 3


# ─── reconcile from fills（ADR-0017 live worker reconcile） ───


def test_from_fills_reconstructs_state() -> None:
    fills = [
        (OrderSide.BUY, 1.0, 100.0, 1000),
        (OrderSide.BUY, 1.0, 110.0, 2000),
        (OrderSide.SELL, 0.5, 120.0, 3000),
    ]
    p = Position.from_fills(_btc(), fills)
    assert p.quantity == 1.5
    assert p.avg_open_price == pytest.approx(105.0)  # (100 + 110) / 2
    assert p.realized_pnl == pytest.approx(0.5 * (120 - 105))  # 减仓 0.5 @ 120
    # 跟手动 apply 三次结果一致
    p2 = Position(instrument_id=_btc())
    for side, qty, price, ts in fills:
        p2.apply_fill(side, qty, price, ts)
    assert p2.quantity == p.quantity
    assert p2.avg_open_price == p.avg_open_price
    assert p2.realized_pnl == p.realized_pnl
