"""D-8c · Factor / Signal / StrategyHint schema + manager 兜底推断单测。

覆盖：
- LLM 给齐 factors / signals / strategy_hint → 透传
- LLM 漏 strategy_hint → manager 按 factors 主导类兜底
- analyst brief 带 factors → manager 兜底从 briefs 收集
- neutral + low confidence → strategy_hint.family = "none"
"""
from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from inalpha_research.manager import build_plan_from_raw
from inalpha_research.schemas import AnalystBrief, Factor


def _as_of() -> datetime:
    return datetime(2026, 5, 22, tzinfo=UTC)


def _brief_with_factors(
    analyst: str,
    stance: str,
    factors: list[Factor],
) -> AnalystBrief:
    return AnalystBrief(
        analyst=analyst,  # type: ignore[arg-type]
        stance=stance,  # type: ignore[arg-type]
        confidence=0.7,
        summary=f"{analyst} brief with factors",
        key_points=[f"{analyst} kp"],
        factors=factors,
    )


# ────────────────────────────────────────────────────────────────────
# 透传：LLM 给齐 factors / signals / strategy_hint
# ────────────────────────────────────────────────────────────────────


def test_plan_passes_through_llm_factors_and_hint() -> None:
    raw = {
        "rating": "overweight",
        "confidence": 0.75,
        "thesis": "T",
        "risks": ["R"],
        "suggested_action": "open_long 0.03",
        "horizon": "swing",
        "factors": [
            {
                "name": "sma20_cross_up",
                "kind": "momentum",
                "value": 1.02,
                "strength": 0.8,
                "horizon": "swing",
                "explanation": "20-bar SMA crossed above 50-bar",
            },
            {
                "name": "rsi_neutral",
                "kind": "mean_reversion",
                "value": 58.0,
                "strength": 0.3,
                "horizon": "swing",
                "explanation": "RSI 58, not overbought",
            },
        ],
        "signals": [
            {
                "direction": "long",
                "strength": 0.7,
                "timeframe": "1h",
                "derived_from": ["sma20_cross_up"],
            }
        ],
        "strategy_hint": {
            "family": "trend",
            "params": {"fast_period": 12, "slow_period": 36, "trade_size": 0.03},
            "reasoning": "momentum dominates",
        },
    }
    plan = build_plan_from_raw(
        raw,
        venue="binance",
        symbol="BTC/USDT",
        timeframe="1h",
        as_of=_as_of(),
        briefs=[],
    )
    assert isinstance(plan.research_id, UUID)
    assert plan.strategy_hint.family == "trend"
    assert plan.strategy_hint.params["fast_period"] == 12
    assert len(plan.factors) == 2
    assert plan.factors[0].name == "sma20_cross_up"
    assert len(plan.signals) == 1
    assert plan.signals[0].direction == "long"


# ────────────────────────────────────────────────────────────────────
# 兜底：LLM 没给 strategy_hint，但 briefs 里有 factors
# ────────────────────────────────────────────────────────────────────


def test_strategy_hint_falls_back_to_trend_from_momentum_briefs() -> None:
    momentum_factor = Factor(
        name="breakout_high",
        kind="momentum",
        value=1.05,
        strength=0.8,
        horizon="swing",
    )
    brief = _brief_with_factors("technical", "bullish", [momentum_factor])
    raw = {
        "rating": "overweight",
        "confidence": 0.7,
        "thesis": "T",
        "suggested_action": "open_long 0.02",
        # no factors / no strategy_hint in LLM response
    }
    plan = build_plan_from_raw(
        raw,
        venue="binance",
        symbol="BTC/USDT",
        timeframe="1h",
        as_of=_as_of(),
        briefs=[brief],
    )
    assert plan.strategy_hint.family == "trend"
    assert "fast_period" in plan.strategy_hint.params
    # factors 应该从 brief 收集
    assert len(plan.factors) == 1
    assert plan.factors[0].name == "breakout_high"


def test_strategy_hint_falls_back_to_mean_reversion() -> None:
    mr_factor = Factor(
        name="rsi_extreme_low",
        kind="mean_reversion",
        value=22.0,
        strength=0.9,
        horizon="intraday",
    )
    raw = {
        "rating": "overweight",
        "confidence": 0.65,
        "thesis": "T",
        "suggested_action": "open_long",
    }
    plan = build_plan_from_raw(
        raw,
        venue="binance",
        symbol="BTC/USDT",
        timeframe="1h",
        as_of=_as_of(),
        briefs=[_brief_with_factors("technical", "bullish", [mr_factor])],
    )
    assert plan.strategy_hint.family == "mean_reversion"
    assert "num_std" in plan.strategy_hint.params


def test_strategy_hint_falls_back_to_buy_hold_for_macro() -> None:
    """D-12 收紧：纯 macro 兜底 buy_hold 需要 overweight + **position** 长线论点。

    raw 不带 horizon（默认 swing）时不再 buy_hold（见 test_manager 的
    test_pure_macro_swing_horizon_routes_to_none）。
    """
    macro_factor = Factor(
        name="halving_phase",
        kind="macro",
        value="post_halving",
        strength=0.7,
        horizon="position",
    )
    raw = {
        "rating": "overweight",
        "confidence": 0.7,
        "thesis": "T",
        "suggested_action": "open_long",
        "horizon": "position",
    }
    plan = build_plan_from_raw(
        raw,
        venue="binance",
        symbol="BTC/USDT",
        timeframe="1d",
        as_of=_as_of(),
        briefs=[_brief_with_factors("fundamental", "bullish", [macro_factor])],
    )
    assert plan.strategy_hint.family == "buy_hold"
    assert plan.strategy_hint.params.get("trade_size", 0) > 0


# ────────────────────────────────────────────────────────────────────
# 兜底：neutral + low confidence → family == "none"
# ────────────────────────────────────────────────────────────────────


def test_neutral_low_confidence_yields_none_family() -> None:
    raw = {
        "rating": "neutral",
        "confidence": 0.4,
        "thesis": "mixed signals",
        "suggested_action": "wait",
    }
    plan = build_plan_from_raw(
        raw,
        venue="binance",
        symbol="BTC/USDT",
        timeframe="1h",
        as_of=_as_of(),
        briefs=[],
    )
    assert plan.strategy_hint.family == "none"
    assert plan.strategy_hint.params == {}


def test_invalid_strategy_hint_falls_back() -> None:
    """LLM 给的 strategy_hint 字段不合法 → 兜底机制启动。"""
    raw = {
        "rating": "overweight",
        "confidence": 0.7,
        "thesis": "T",
        "suggested_action": "open_long 0.02",
        "strategy_hint": {"family": "not_a_real_family", "params": "not_a_dict"},
    }
    plan = build_plan_from_raw(
        raw,
        venue="binance",
        symbol="BTC/USDT",
        timeframe="1h",
        as_of=_as_of(),
        briefs=[],
    )
    # invalid family + empty factors → none
    assert plan.strategy_hint.family == "none"


# ────────────────────────────────────────────────────────────────────
# research_id 默认生成 + 可指定
# ────────────────────────────────────────────────────────────────────


def test_research_id_auto_generated() -> None:
    raw = {
        "rating": "neutral",
        "confidence": 0.5,
        "thesis": "T",
        "suggested_action": "wait",
    }
    plan_a = build_plan_from_raw(
        raw, venue="binance", symbol="BTC/USDT",
        timeframe="1h", as_of=_as_of(), briefs=[],
    )
    plan_b = build_plan_from_raw(
        raw, venue="binance", symbol="BTC/USDT",
        timeframe="1h", as_of=_as_of(), briefs=[],
    )
    assert plan_a.research_id != plan_b.research_id
