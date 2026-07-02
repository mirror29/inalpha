"""``spot_guard`` 纯函数边界单测。

- ``violates_spot_long_only``:禁裸空 / 禁超卖翻空(与回测 ``can_afford_sell`` 同口径)
- ``violates_spot_buying_power``:禁透支买穿现金池(与回测 ``can_afford_buy`` 同口径)
"""
from __future__ import annotations

from decimal import Decimal

from inalpha_paper.execution.spot_guard import (
    violates_spot_buying_power,
    violates_spot_long_only,
)


def test_buy_never_violates() -> None:
    # BUY 不归本守门管（现金透支守门是兄弟 gap）
    assert violates_spot_long_only(side="BUY", quantity=999, current_qty=0) is False


def test_flat_sell_violates() -> None:
    # 空仓 SELL = 裸空 → 违规
    assert violates_spot_long_only(side="SELL", quantity=1.0, current_qty=0) is True


def test_sell_equal_holding_ok() -> None:
    # 等量平多 → 放行
    assert violates_spot_long_only(side="SELL", quantity=1.0, current_qty=1.0) is False


def test_sell_partial_ok() -> None:
    # 减仓（卖出量少于持仓）→ 放行
    assert violates_spot_long_only(side="SELL", quantity=0.5, current_qty=1.0) is False


def test_oversell_violates() -> None:
    # 超卖翻空（卖出量 > 持仓）→ 违规（d4404933 漂移的直接成因）
    assert violates_spot_long_only(side="SELL", quantity=2.0, current_qty=1.0) is True


def test_sell_against_existing_short_violates() -> None:
    # 已是空仓再 SELL（加空）→ current<0，任何正 qty 都违规
    assert (
        violates_spot_long_only(side="SELL", quantity=1.0, current_qty=Decimal("-2.0"))
        is True
    )


# ─── violates_spot_buying_power ───


def _bp(**overrides: object) -> bool:
    kwargs: dict = dict(
        side="BUY",
        quantity=1.0,
        ref_price=100.0,
        fee_rate=0.001,
        order_ccy_rate=Decimal(1),
        available_cash_base=Decimal(10_000),
        trading_mode="spot",
    )
    kwargs.update(overrides)
    return violates_spot_buying_power(**kwargs)


def test_bp_sell_never_violates() -> None:
    # SELL 不归本守门管（裸空由 long-only 守门管）
    assert _bp(side="SELL", quantity=999_999) is False


def test_bp_perp_never_violates() -> None:
    # perp 走保证金购买力校验,本守门短路
    assert _bp(trading_mode="perp", quantity=999_999) is False


def test_bp_within_available_ok() -> None:
    # 100.1(含 fee)≤ 10000×0.99 → 放行
    assert _bp() is False


def test_bp_exceeds_available_violates() -> None:
    # 200×100×1.001 = 20020 > 9900 → 拒
    assert _bp(quantity=200.0) is True


def test_bp_safety_factor_boundary() -> None:
    # 恰好压线:可用 10000,notional+fee 落在 (9900, 10000] 区间 → 仍拒(留 1% buffer)
    assert _bp(quantity=99.5) is True  # 99.5×100×1.001 = 9959.95 > 9900


def test_bp_negative_available_rejects_any_buy() -> None:
    # 折算可用现金已为负(已透支)→ 任何 BUY 必拒
    assert _bp(quantity=0.0001, available_cash_base=Decimal(-1)) is True


def test_bp_missing_rate_fail_closed() -> None:
    # 订单计价货币汇率拿不到 → fail-closed 拒单(算不出值多少 base,宁拒不猜)
    assert _bp(order_ccy_rate=None, quantity=0.0001) is True


def test_bp_cross_currency_rate_applied() -> None:
    # 汇率参与折算:notional 700(CNY)×rate 0.14 = 98(USD)+fee ≤ 9900 → 放行
    assert (
        _bp(quantity=7.0, ref_price=100.0, order_ccy_rate=Decimal("0.14")) is False
    )
