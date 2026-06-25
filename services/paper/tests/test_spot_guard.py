"""``spot_guard.violates_spot_long_only`` 纯函数边界单测（禁裸空 / 禁超卖翻空）。

与回测 ``Portfolio.can_afford_sell`` 同口径:SELL 量严格大于当前 LONG 持仓即违规。
"""
from __future__ import annotations

from decimal import Decimal

from inalpha_paper.execution.spot_guard import violates_spot_long_only


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
