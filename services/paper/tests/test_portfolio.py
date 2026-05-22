"""Portfolio 单测 —— 重点 round-trip / flip / mark-to-market。

补 D-8b' review 找到的高风险盲区：
- `_handle_fill` 在反向开仓（flip）时漏记 `closed_trade_pnls`
- `closed_trade_pnls` 应该正负配对，否则 win_rate 错算
"""
from __future__ import annotations

from inalpha_paper.engine.portfolio import Portfolio
from inalpha_paper.kernel.identifiers import InstrumentId
from inalpha_paper.kernel.msgbus import MessageBus
from inalpha_paper.model.events import OrderFilled
from inalpha_paper.model.orders import OrderSide


def _btc() -> InstrumentId:
    return InstrumentId(symbol="BTC/USDT", venue="binance")


def _fill(side: OrderSide, qty: float, price: float, ts: int = 1_000) -> OrderFilled:
    """造一个 OrderFilled 事件，instrument_id 写死 BTC/USDT。"""
    inst = _btc()
    return OrderFilled(
        client_order_id=f"cid-{ts}",  # type: ignore[arg-type]
        venue_order_id=None,
        instrument_id=inst,
        strategy_id="test-strat",  # type: ignore[arg-type]
        side=side,
        fill_quantity=qty,
        fill_price=price,
        ts_event=ts,
        ts_init=ts,
    )


def _new_portfolio(initial_cash: float = 10_000.0, fee_rate: float = 0.0) -> Portfolio:
    bus = MessageBus()
    return Portfolio(bus, initial_cash=initial_cash, fee_rate=fee_rate)


# ────────────────────────────────────────────────────────────────────
# round-trip 基础：BUY → SELL 闭环
# ────────────────────────────────────────────────────────────────────


def test_long_open_then_close_records_one_round_trip_pnl() -> None:
    """开多 → 平多 → 1 笔 round-trip。"""
    p = _new_portfolio()
    p._handle_fill(_fill(OrderSide.BUY, qty=1.0, price=100.0))
    p._handle_fill(_fill(OrderSide.SELL, qty=1.0, price=110.0))
    pnls = p.closed_trade_pnls
    assert len(pnls) == 1
    assert abs(pnls[0] - 10.0) < 1e-9  # +10 价差盈亏


def test_long_loss_recorded_correctly() -> None:
    p = _new_portfolio()
    p._handle_fill(_fill(OrderSide.BUY, qty=1.0, price=100.0))
    p._handle_fill(_fill(OrderSide.SELL, qty=1.0, price=90.0))
    pnls = p.closed_trade_pnls
    assert len(pnls) == 1
    assert abs(pnls[0] - (-10.0)) < 1e-9


# ────────────────────────────────────────────────────────────────────
# 反向开仓（flip）—— D-8b' review 修复的高风险 bug
# ────────────────────────────────────────────────────────────────────


def test_flip_long_to_short_records_round_trip_for_closed_leg() -> None:
    """开多 1 → 直接卖 2 = 平掉 long + 反向开 short 1。

    旧 bug：closed_trade_pnls 是空数组（PositionChanged 分支跳过入账）。
    修复后：应该有 1 笔（平掉的 long leg），值 = (110-100) * 1 = +10。
    """
    p = _new_portfolio()
    p._handle_fill(_fill(OrderSide.BUY, qty=1.0, price=100.0))
    # 反向开仓
    p._handle_fill(_fill(OrderSide.SELL, qty=2.0, price=110.0))

    # 此时仓位应是 short 1 unit @ avg 110
    pos = p.position(_btc())
    assert pos is not None
    assert pos.quantity == -1.0
    assert abs(pos.avg_open_price - 110.0) < 1e-9

    # 关键断言：closed_trade_pnls 已经入账了 long leg 的 +10
    pnls = p.closed_trade_pnls
    assert len(pnls) == 1, f"expected 1 round-trip after flip, got {len(pnls)}"
    assert abs(pnls[0] - 10.0) < 1e-9


def test_flip_short_to_long_records_round_trip() -> None:
    """开空 1 → 直接买 2 = 平掉 short + 反向开 long 1。"""
    p = _new_portfolio()
    p._handle_fill(_fill(OrderSide.SELL, qty=1.0, price=100.0))
    p._handle_fill(_fill(OrderSide.BUY, qty=2.0, price=90.0))

    pos = p.position(_btc())
    assert pos is not None
    assert pos.quantity == 1.0

    pnls = p.closed_trade_pnls
    assert len(pnls) == 1
    # short 100 → buy back 90 = +10
    assert abs(pnls[0] - 10.0) < 1e-9


def test_flip_then_close_records_two_round_trips() -> None:
    """开多 → flip 到 short → 平掉 short = 2 笔 round-trip。"""
    p = _new_portfolio()
    p._handle_fill(_fill(OrderSide.BUY, qty=1.0, price=100.0))
    p._handle_fill(_fill(OrderSide.SELL, qty=2.0, price=110.0))  # flip
    p._handle_fill(_fill(OrderSide.BUY, qty=1.0, price=105.0))  # close short

    pos = p.position(_btc())
    assert pos is not None
    assert pos.is_flat

    pnls = p.closed_trade_pnls
    assert len(pnls) == 2
    # 第 1 笔：long 100 → exit 110 = +10
    assert abs(pnls[0] - 10.0) < 1e-9
    # 第 2 笔：short 110 → buy back 105 = +5
    assert abs(pnls[1] - 5.0) < 1e-9


def test_partial_close_then_full_close_one_round_trip() -> None:
    """开多 2 → 减仓 1（仍持多 1）→ 平掉 1。"""
    p = _new_portfolio()
    p._handle_fill(_fill(OrderSide.BUY, qty=2.0, price=100.0))
    p._handle_fill(_fill(OrderSide.SELL, qty=1.0, price=110.0))  # 减仓非 flip
    pos = p.position(_btc())
    assert pos is not None
    assert pos.quantity == 1.0
    # 减仓时还没完全平 → 不入账
    assert len(p.closed_trade_pnls) == 0

    p._handle_fill(_fill(OrderSide.SELL, qty=1.0, price=120.0))  # 完全平掉
    pnls = p.closed_trade_pnls
    assert len(pnls) == 1
    # 累计 realized_pnl = (110-100) + (120-100) = 30
    assert abs(pnls[0] - 30.0) < 1e-9


def test_add_to_position_no_round_trip() -> None:
    """开多 1 → 加仓 1（同方向加仓不入账）。"""
    p = _new_portfolio()
    p._handle_fill(_fill(OrderSide.BUY, qty=1.0, price=100.0))
    p._handle_fill(_fill(OrderSide.BUY, qty=1.0, price=110.0))
    pos = p.position(_btc())
    assert pos is not None
    assert pos.quantity == 2.0
    # 同方向加仓 → 加权平均 avg_open_price=105；不入账
    assert len(p.closed_trade_pnls) == 0


# ────────────────────────────────────────────────────────────────────
# fee + cash 会计
# ────────────────────────────────────────────────────────────────────


def test_fees_are_charged_and_excluded_from_round_trip_pnl() -> None:
    """fee 进 total_fees，**不**进 round-trip PnL（保持 round-trip 是纯价差）。"""
    p = _new_portfolio(fee_rate=0.001)  # 0.1%
    p._handle_fill(_fill(OrderSide.BUY, qty=1.0, price=100.0))  # 0.1 fee
    p._handle_fill(_fill(OrderSide.SELL, qty=1.0, price=110.0))  # 0.11 fee
    assert abs(p.total_fees - 0.21) < 1e-9
    pnls = p.closed_trade_pnls
    assert abs(pnls[0] - 10.0) < 1e-9  # 价差 10，不扣 fee


def test_buy_decreases_cash_by_notional_plus_fee() -> None:
    p = _new_portfolio(initial_cash=10_000.0, fee_rate=0.001)
    p._handle_fill(_fill(OrderSide.BUY, qty=0.5, price=200.0))
    # cash = 10000 - 100 - 0.1 = 9899.9
    assert abs(p.cash - 9899.9) < 1e-9


def test_sell_increases_cash_by_notional_minus_fee() -> None:
    p = _new_portfolio(initial_cash=10_000.0, fee_rate=0.001)
    p._handle_fill(_fill(OrderSide.BUY, qty=1.0, price=100.0))  # 9899.9 left
    p._handle_fill(_fill(OrderSide.SELL, qty=1.0, price=110.0))  # gain 109.89
    # 9899.9 + 110 - 0.11 = 10009.79
    assert abs(p.cash - 10009.79) < 1e-9


# ────────────────────────────────────────────────────────────────────
# mark-to-market equity
# ────────────────────────────────────────────────────────────────────


def test_equity_uses_latest_mark_for_open_position() -> None:
    p = _new_portfolio()
    p._handle_fill(_fill(OrderSide.BUY, qty=1.0, price=100.0))
    p.update_mark(_btc(), 110.0)
    # equity = cash(9900) + qty(1) * mark(110) = 10010
    assert abs(p.equity() - 10_010.0) < 1e-9


def test_equity_falls_back_to_avg_when_no_mark() -> None:
    """update_mark 没调过时用 avg_open_price 兜底。"""
    p = _new_portfolio()
    p._handle_fill(_fill(OrderSide.BUY, qty=1.0, price=100.0))
    # 没 update_mark → 用 avg=100
    assert abs(p.equity() - 10_000.0) < 1e-9


def test_equity_zero_when_flat() -> None:
    p = _new_portfolio()
    assert abs(p.equity() - 10_000.0) < 1e-9
