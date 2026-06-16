"""Research manager 单测 —— 用 FakeLLM 测综合逻辑 + 边界。"""
from __future__ import annotations

from datetime import UTC, datetime

from inalpha_research.llm.client import FakeLLMClient
from inalpha_research.manager import (
    ResearchManager,
    briefs_to_compact_text,
    build_plan_from_raw,
)
from inalpha_research.schemas import AnalystBrief, Factor, ResearchPlan


def _as_of() -> datetime:
    return datetime(2026, 5, 21, tzinfo=UTC)


def _brief(analyst: str, stance: str = "neutral") -> AnalystBrief:
    return AnalystBrief(
        analyst=analyst,  # type: ignore[arg-type]
        stance=stance,  # type: ignore[arg-type]
        confidence=0.6,
        summary=f"{analyst} brief",
        key_points=[f"{analyst} kp"],
    )


# ────────────────────────────────────────────────────────────────────
# synthesize · happy path
# ────────────────────────────────────────────────────────────────────


async def test_synthesize_returns_validated_plan() -> None:
    llm = FakeLLMClient(
        {
            "research manager": {
                "rating": "overweight",
                "confidence": 0.7,
                "thesis": "T",
                "risks": ["R1", "R2"],
                "suggested_action": "open_long 0.02",
                "horizon": "swing",
            }
        }
    )
    mgr = ResearchManager(llm=llm)
    plan = await mgr.synthesize(
        venue="binance",
        symbol="BTC/USDT",
        timeframe="1h",
        as_of=_as_of(),
        briefs=[_brief("technical", "bullish"), _brief("fundamental", "neutral")],
        user_question="should I buy BTC?",
    )

    assert isinstance(plan, ResearchPlan)
    assert plan.rating == "overweight"
    assert plan.confidence == 0.7
    assert plan.risks == ["R1", "R2"]
    assert plan.suggested_action == "open_long 0.02"
    assert plan.horizon == "swing"
    # 原 briefs 必须保留在 plan.briefs 里供前端 / trader 引用
    assert len(plan.briefs) == 2

    # 检查 user prompt 里带了 user_question + briefs
    assert len(llm.calls) == 1
    user_prompt = llm.calls[0]["user"]
    assert "user_original_question" in user_prompt
    assert "should I buy BTC?" in user_prompt
    assert "[technical]" in user_prompt
    assert "[fundamental]" in user_prompt


async def test_synthesize_degrades_when_llm_fails() -> None:
    """manager LLM 调用失败（LLMError）→ 降级返 neutral plan，不抛 502，且保留 briefs。

    回归 ADR-0037 调试发现的 deep_dive 502：manager 综合那次 LLM 调用（截断 / 抽风）
    失败时，必须降级而不是把整条链路 502 掉、丢光 analyst 成果。
    ``FakeLLMClient({})`` 对任何 system 都不匹配 → ``complete_json`` 抛 ``LLMError``。
    """
    llm = FakeLLMClient({})  # 任何 system 都不命中 → 抛 LLMError
    mgr = ResearchManager(llm=llm)
    briefs = [_brief("technical", "bullish"), _brief("persona_buffett", "bearish")]
    plan = await mgr.synthesize(
        venue="binance",
        symbol="BTC/USDT",
        timeframe="1h",
        as_of=_as_of(),
        briefs=briefs,
    )

    assert isinstance(plan, ResearchPlan)
    assert plan.rating == "neutral"
    assert plan.confidence == 0.0
    # analyst 成果不丢
    assert {b.analyst for b in plan.briefs} == {"technical", "persona_buffett"}
    # thesis 明示综合失败（而非伪装成正常结论）；用英文占位（CLAUDE.md §3，与 system 一致）
    assert "Synthesis unavailable" in plan.thesis
    assert plan.suggested_action == "wait"


async def test_fallback_does_not_pull_brief_factors_into_strategy() -> None:
    """manager 失败兜底**不**把 briefs 的 factor 拉进来 → 不会被 kind=macro 误导出策略族。

    回归 CR：persona/valuation factor 都是 kind=macro，若兜底拉进来，_dominant_kind 多数
    投票会偏 macro → strategy 兜底成 buy_hold。综合本就失败，不该再据此推策略族。
    """
    macro_factor = Factor(
        name="halving_phase",
        kind="macro",
        value="post_halving",
        strength=0.5,
        horizon="position",
        explanation="within 12mo of halving",
    )
    brief = AnalystBrief(
        analyst="persona_buffett",  # type: ignore[arg-type]
        stance="bullish",
        confidence=0.6,
        summary="value lens",
        key_points=["moat"],
        factors=[macro_factor],
    )
    llm = FakeLLMClient({})  # 综合失败 → 走 _fallback_raw
    plan = await ResearchManager(llm=llm).synthesize(
        venue="binance",
        symbol="BTC/USDT",
        timeframe="1h",
        as_of=_as_of(),
        briefs=[brief],
    )
    # 兜底 plan 不带任何 factor（不再从 brief 拉 macro factor）
    assert plan.factors == []
    # 因此 strategy 不会被 macro 误导成 buy_hold
    assert plan.strategy_hint.family == "none"


# ────────────────────────────────────────────────────────────────────
# build_plan_from_raw · 兜底逻辑
# ────────────────────────────────────────────────────────────────────


def test_build_plan_falls_back_on_invalid_rating() -> None:
    """LLM 给乱来的 rating → 兜底 neutral。"""
    plan = build_plan_from_raw(
        {"rating": "very-overweight"},
        venue="binance",
        symbol="BTC/USDT",
        timeframe="1h",
        as_of=_as_of(),
        briefs=[],
    )
    assert plan.rating == "neutral"


def test_build_plan_falls_back_on_invalid_horizon() -> None:
    plan = build_plan_from_raw(
        {"rating": "neutral", "horizon": "ultra-long"},
        venue="binance",
        symbol="BTC/USDT",
        timeframe="1h",
        as_of=_as_of(),
        briefs=[],
    )
    assert plan.horizon == "swing"


def test_build_plan_empty_thesis_gets_placeholder() -> None:
    plan = build_plan_from_raw(
        {},
        venue="binance",
        symbol="BTC/USDT",
        timeframe="1h",
        as_of=_as_of(),
        briefs=[],
    )
    assert plan.thesis == "(no thesis)"
    assert plan.suggested_action == "wait"
    assert plan.confidence == 0.5


def test_build_plan_clamps_confidence_to_unit_range() -> None:
    """LLM 返 1.5 / -0.3 时 _clamp_unit 兜底（review B2 fix）。"""
    plan = build_plan_from_raw(
        {"confidence": 1.5, "rating": "neutral"},
        venue="binance",
        symbol="BTC/USDT",
        timeframe="1h",
        as_of=_as_of(),
        briefs=[],
    )
    assert plan.confidence == 1.0

    plan2 = build_plan_from_raw(
        {"confidence": -0.3, "rating": "neutral"},
        venue="binance",
        symbol="BTC/USDT",
        timeframe="1h",
        as_of=_as_of(),
        briefs=[],
    )
    assert plan2.confidence == 0.0


def test_build_plan_non_numeric_confidence_falls_back() -> None:
    plan = build_plan_from_raw(
        {"confidence": "very-high", "rating": "neutral"},
        venue="binance",
        symbol="BTC/USDT",
        timeframe="1h",
        as_of=_as_of(),
        briefs=[],
    )
    assert plan.confidence == 0.5  # fallback default


# ────────────────────────────────────────────────────────────────────
# strategy_hint 兜底路由（D-12：macro 不再一刀切 buy_hold）
# ────────────────────────────────────────────────────────────────────


def _factor(name: str, kind: str, strength: float) -> dict:
    return {
        "name": name,
        "kind": kind,
        "value": 1.0,
        "strength": strength,
        "horizon": "swing",
        "explanation": f"{name} test factor",
    }


def test_macro_dominant_with_secondary_momentum_routes_to_trend() -> None:
    """macro 主导 + 次强 momentum → trend 当交易引擎，sizing 体现宏观 regime。"""
    plan = build_plan_from_raw(
        {
            "rating": "overweight",
            "confidence": 0.7,
            "factors": [
                _factor("risk_on_regime", "macro", 0.9),
                _factor("dovish_tailwind", "macro", 0.8),
                _factor("sma_upcross", "momentum", 0.6),
            ],
        },
        venue="binance",
        symbol="BTC/USDT",
        timeframe="1h",
        as_of=_as_of(),
        briefs=[],
    )
    assert plan.strategy_hint.family == "trend"
    assert "sizing" in plan.strategy_hint.reasoning
    # trade_size 随 confidence 缩放（0.04 * 0.7 = 0.028）
    assert plan.strategy_hint.params["trade_size"] == 0.028


def test_pure_macro_overweight_position_routes_to_buy_hold() -> None:
    """纯 macro + overweight + position 长线论点 → 仍允许 buy_hold。"""
    plan = build_plan_from_raw(
        {
            "rating": "overweight",
            "confidence": 0.7,
            "horizon": "position",
            "factors": [_factor("structural_adoption", "macro", 0.8)],
        },
        venue="binance",
        symbol="BTC/USDT",
        timeframe="1h",
        as_of=_as_of(),
        briefs=[],
    )
    assert plan.strategy_hint.family == "buy_hold"


def test_pure_macro_underweight_routes_to_none_not_buy_hold() -> None:
    """看空（underweight）绝不该兜底成买入持有——旧逻辑的 bug 回归。"""
    plan = build_plan_from_raw(
        {
            "rating": "underweight",
            "confidence": 0.7,
            "factors": [_factor("hawkish_squeeze", "macro", 0.8)],
        },
        venue="binance",
        symbol="BTC/USDT",
        timeframe="1h",
        as_of=_as_of(),
        briefs=[],
    )
    assert plan.strategy_hint.family == "none"


def test_pure_macro_swing_horizon_routes_to_none() -> None:
    """纯 macro + swing（非长线）→ 无技术引擎可用 → none，不强行持有。"""
    plan = build_plan_from_raw(
        {
            "rating": "overweight",
            "confidence": 0.7,
            "horizon": "swing",
            "factors": [_factor("risk_on_regime", "macro", 0.8)],
        },
        venue="binance",
        symbol="BTC/USDT",
        timeframe="1h",
        as_of=_as_of(),
        briefs=[],
    )
    assert plan.strategy_hint.family == "none"


def test_briefs_to_compact_text_is_json() -> None:
    import json

    text = briefs_to_compact_text([_brief("technical")])
    parsed = json.loads(text)
    assert isinstance(parsed, list)
    assert parsed[0]["analyst"] == "technical"
