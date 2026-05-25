"""Research manager 单测 —— 用 FakeLLM 测综合逻辑 + 边界。"""
from __future__ import annotations

from datetime import UTC, datetime

from inalpha_research.llm.client import FakeLLMClient
from inalpha_research.manager import (
    ResearchManager,
    briefs_to_compact_text,
    build_plan_from_raw,
)
from inalpha_research.schemas import AnalystBrief, ResearchPlan


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


def test_briefs_to_compact_text_is_json() -> None:
    import json

    text = briefs_to_compact_text([_brief("technical")])
    parsed = json.loads(text)
    assert isinstance(parsed, list)
    assert parsed[0]["analyst"] == "technical"
