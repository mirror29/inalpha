"""``check_order_notional`` 单笔名义价值硬上限闸门单测（issue #42）。

纯同步无状态函数，不依赖 DB——用最小 stub 暴露 ``max_order_notional`` 即可。

测试矩阵：

- ``factory=None``（风控禁用）→ pass-through，不抛
- ``max_order_notional=None``（未配上限）→ pass-through，不抛
- notional 低于 / 恰等于上限 → 不抛（边界含等号放行）
- notional 高于上限 → ``ConflictError(code='RISK_REJECTED', rule_name='MaxOrderNotional')``
- 负 quantity（做空）按 ``abs`` 计 → 超限照样抛
- ``RiskRulesConfig`` 解析 / 校验 ``max_order_notional``
"""
from __future__ import annotations

from dataclasses import dataclass

import pytest
from inalpha_shared.errors import ConflictError

from inalpha_paper.execution.risk_guard import check_order_notional
from inalpha_paper.execution.risk_rules.config import RiskRulesConfig


@dataclass
class _StubFactory:
    """只暴露 ``max_order_notional`` 的最小 duck-typed factory（check_order_notional 仅读这个）。"""

    max_order_notional: float | None


def _call(factory: object, *, quantity: float, ref_price: float) -> None:
    check_order_notional(
        factory,  # type: ignore[arg-type]
        quantity=quantity,
        ref_price=ref_price,
        venue="binance",
        symbol="BTC/USDT",
    )


def test_factory_none_passes_through() -> None:
    """风控禁用（factory=None）→ 不校验，不抛。"""
    _call(None, quantity=1e9, ref_price=100.0)


def test_cap_none_passes_through() -> None:
    """未配 max_order_notional → pass-through，即便天量也放行。"""
    _call(_StubFactory(max_order_notional=None), quantity=1e9, ref_price=100.0)


def test_under_cap_passes() -> None:
    """notional 低于上限 → 放行。"""
    _call(_StubFactory(max_order_notional=100_000.0), quantity=0.5, ref_price=50_000.0)


def test_exactly_at_cap_passes() -> None:
    """恰等于上限 → 放行（边界含等号）。"""
    _call(_StubFactory(max_order_notional=100_000.0), quantity=2.0, ref_price=50_000.0)


def test_over_cap_raises() -> None:
    """超上限 → ConflictError，rule_name=MaxOrderNotional，带 notional / cap 细节。"""
    with pytest.raises(ConflictError) as ei:
        _call(_StubFactory(max_order_notional=100_000.0), quantity=3.0, ref_price=50_000.0)
    err = ei.value
    assert err.code == "RISK_REJECTED"
    assert err.details["rule_name"] == "MaxOrderNotional"
    assert err.details["notional"] == pytest.approx(150_000.0)
    assert err.details["max_order_notional"] == pytest.approx(100_000.0)


def test_short_uses_abs_quantity() -> None:
    """负 quantity（做空意图）按绝对值算 notional，超限照样拦。"""
    with pytest.raises(ConflictError):
        _call(_StubFactory(max_order_notional=100_000.0), quantity=-3.0, ref_price=50_000.0)


def test_config_parses_max_order_notional() -> None:
    """RiskRulesConfig 解析 max_order_notional；未配默认 None。"""
    assert RiskRulesConfig().max_order_notional is None
    cfg = RiskRulesConfig.model_validate({"max_order_notional": 50_000.0})
    assert cfg.max_order_notional == pytest.approx(50_000.0)


def test_config_rejects_non_positive_cap() -> None:
    """max_order_notional 必须 > 0（Pydantic gt=0）。"""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        RiskRulesConfig.model_validate({"max_order_notional": 0})
