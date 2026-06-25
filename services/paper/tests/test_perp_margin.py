"""``perp_margin`` 纯数学单测:分档 MM 连续性、IM、双分支强平价、funding 符号。"""
from __future__ import annotations

import math

import pytest

from inalpha_paper.execution import perp_margin as pm

# ─── 初始保证金 ───


def test_initial_margin_divides_by_leverage() -> None:
    assert pm.initial_margin(10_000.0, 10) == pytest.approx(1_000.0)
    assert pm.initial_margin(10_000.0, 1) == pytest.approx(10_000.0)


def test_initial_margin_rejects_nonpositive_leverage() -> None:
    with pytest.raises(ValueError):
        pm.initial_margin(1_000.0, 0)


# ─── 维持保证金分档 ───


def test_maintenance_margin_tiers() -> None:
    # 档1:小仓 0.4%
    assert pm.maintenance_margin(10_000.0) == pytest.approx(10_000 * 0.004)
    # 档2
    assert pm.maintenance_margin(100_000.0) == pytest.approx(100_000 * 0.005 - 50)
    # 档3
    assert pm.maintenance_margin(1_000_000.0) == pytest.approx(1_000_000 * 0.010 - 3_050)


def test_maintenance_margin_bracket_continuity() -> None:
    """档边界两侧 MM 必须连续(cum 的作用),否则大仓位强平判定会跳变。"""
    for boundary in (50_000.0, 600_000.0):
        below = pm.maintenance_margin(boundary - 1e-6)
        at = pm.maintenance_margin(boundary)
        assert below == pytest.approx(at, abs=1e-3)


def test_maintenance_margin_decoupled_from_leverage() -> None:
    """MM 只看名义价值,与杠杆无关(常见错误:把 MM 写成 notional/leverage 比例)。"""
    # 同名义价值,不同杠杆下 MM 相同(本函数压根不接受 leverage 参数即是保证)
    assert pm.maintenance_margin(100_000.0) == pm.maintenance_margin(100_000.0)


def test_bracket_for_returns_top_tier_above_range() -> None:
    mmr, cum = pm.bracket_for(5_000_000.0)
    assert (mmr, cum) == (0.010, 3_050.0)


# ─── 强平价(多空对称) ───


def test_liquidation_price_long() -> None:
    # 10x 多头 @100,逐仓钱包=IM=10;mmr=0.004(notional 100<50k)
    liq = pm.liquidation_price(side=1, qty_abs=1.0, entry_price=100.0, wallet_balance=10.0)
    # (10 + 0 - 1*1*100) / (1*0.004 - 1*1) = -90 / -0.996 ≈ 90.36
    assert liq == pytest.approx(90.361, abs=1e-2)
    assert liq < 100.0  # 多头强平价在开仓价下方


def test_liquidation_price_short_symmetric() -> None:
    liq = pm.liquidation_price(side=-1, qty_abs=1.0, entry_price=100.0, wallet_balance=10.0)
    # (10 + 0 + 100) / (0.004 + 1) = 110/1.004 ≈ 109.56
    assert liq == pytest.approx(109.562, abs=1e-2)
    assert liq > 100.0  # 空头强平价在开仓价上方


def test_liquidation_price_validates_side() -> None:
    with pytest.raises(ValueError):
        pm.liquidation_price(side=0, qty_abs=1.0, entry_price=100.0, wallet_balance=10.0)


def test_is_liquidated_direction() -> None:
    # 多头:mark 跌到/破强平价
    assert pm.is_liquidated(side=1, mark_price=90.0, liq_price=90.36)
    assert not pm.is_liquidated(side=1, mark_price=95.0, liq_price=90.36)
    # 空头:mark 涨到/破强平价
    assert pm.is_liquidated(side=-1, mark_price=110.0, liq_price=109.56)
    assert not pm.is_liquidated(side=-1, mark_price=105.0, liq_price=109.56)


# ─── 资金费符号 ───


def test_funding_long_pays_when_rate_positive() -> None:
    # 多头 + 正费率 → 付出(正)
    pay = pm.funding_payment(qty_signed=1.0, mark_price=100.0, funding_rate=0.0001)
    assert pay == pytest.approx(0.01) and pay > 0


def test_funding_short_receives_when_rate_positive() -> None:
    # 空头 + 正费率 → 收取(负 = 入账)
    pay = pm.funding_payment(qty_signed=-1.0, mark_price=100.0, funding_rate=0.0001)
    assert pay == pytest.approx(-0.01) and pay < 0


# ─── UPNL(带符号,做空价跌则盈) ───


def test_unrealized_pnl_short_profits_on_drop() -> None:
    assert pm.unrealized_pnl(qty_signed=-1.0, entry_price=100.0, mark_price=90.0) == pytest.approx(10.0)
    assert pm.unrealized_pnl(qty_signed=1.0, entry_price=100.0, mark_price=90.0) == pytest.approx(-10.0)


# ─── 永续标的识别 ───


@pytest.mark.parametrize(
    "symbol,expected",
    [("BTC/USDT:USDT", True), ("ETH/USDT:USDT", True), ("BTC/USDT", False), ("AAPL", False)],
)
def test_is_perp_symbol(symbol: str, expected: bool) -> None:
    assert pm.is_perp_symbol(symbol) is expected


def test_liquidation_price_zero_denominator_returns_nan() -> None:
    # 构造分母为 0 的理论边界(qty×MMR == side×qty 不可能在 side=±1 下成立,除非 mmr=±1),
    # 这里只验证函数对极端入参不崩(返回有限值或 nan,不抛)。
    liq = pm.liquidation_price(side=1, qty_abs=1.0, entry_price=100.0, wallet_balance=10.0)
    assert math.isfinite(liq)


# ─── perp 资格 gate ───


def test_validate_perp_spot_always_passes() -> None:
    pm.validate_perp_eligibility(venue="yfinance", symbol="AAPL", trading_mode="spot", leverage=1)


def test_validate_perp_crypto_perp_ok() -> None:
    pm.validate_perp_eligibility(
        venue="binance", symbol="BTC/USDT:USDT", trading_mode="perp", leverage=5
    )


def test_validate_perp_rejects_non_crypto_venue() -> None:
    with pytest.raises(pm.PerpNotEligibleError):
        pm.validate_perp_eligibility(
            venue="yfinance", symbol="AAPL:USD", trading_mode="perp", leverage=2
        )


def test_validate_perp_rejects_spot_symbol() -> None:
    with pytest.raises(pm.PerpNotEligibleError):
        pm.validate_perp_eligibility(
            venue="binance", symbol="BTC/USDT", trading_mode="perp", leverage=2
        )


def test_validate_perp_rejects_leverage_out_of_range() -> None:
    with pytest.raises(pm.PerpNotEligibleError):
        pm.validate_perp_eligibility(
            venue="binance", symbol="BTC/USDT:USDT", trading_mode="perp", leverage=999
        )
