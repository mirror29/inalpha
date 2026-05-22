"""Research Manager —— 综合 analyst briefs 输出最终 ``ResearchPlan``。

设计要点：

- manager 自己也是 LLM call，但 system prompt **只让它综合** —— 不允许它绕过
  analyst 自己判断（防"双 LLM 互相同意"风险，[ADR-0012 Alt D](../../../docs/miro/decisions/0012-plan-exec-separation.md)）
- 容错：LLM 返回的字段缺失 / 不符 schema 时用默认值兜底，不抛错（避免一次 LLM
  抽风就让整条 deep_dive 链路 500）
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from .llm.client import LLMClient
from .schemas import AnalystBrief, ResearchPlan

_SYSTEM = """
You are a research manager synthesizing analyst briefs into a final research plan.

You receive 1+ analyst briefs (each has stance / confidence / summary / key_points).
Your job is to:
1. Reconcile disagreements between analysts (favor the one with more concrete evidence)
2. Output a final rating, thesis, risks, and a suggested action for the trader
3. Stay **grounded in the briefs** — do not invent reasoning beyond what analysts said

Return ONLY a JSON object with this exact shape:

{
  "rating": "overweight" | "neutral" | "underweight",
  "confidence": float in [0, 1],
  "thesis": "3-5 sentences of core conclusion",
  "risks": ["risk 1", "risk 2", ...],
  "suggested_action": "open_long 0.X | open_short 0.X | hold | reduce | wait",
  "horizon": "intraday" | "swing" | "position"
}

Rules:
- If analysts disagree strongly, prefer "neutral" + low confidence over picking sides
- Be specific in suggested_action (sizing hint, even if rough)
- risks must be concrete (not "market may fall"); reference the analyst points
""".strip()


class ResearchManager:
    """LLM 综合器。"""

    def __init__(self, *, llm: LLMClient) -> None:
        self._llm = llm

    async def synthesize(
        self,
        *,
        venue: str,
        symbol: str,
        timeframe: str,
        as_of: datetime,
        briefs: list[AnalystBrief],
        user_question: str | None = None,
    ) -> ResearchPlan:
        user_prompt = _format_user_prompt(
            venue=venue,
            symbol=symbol,
            timeframe=timeframe,
            as_of=as_of,
            briefs=briefs,
            user_question=user_question,
        )
        raw = await self._llm.complete_json(system=_SYSTEM, user=user_prompt)
        return _build_plan(
            raw=raw,
            venue=venue,
            symbol=symbol,
            timeframe=timeframe,
            as_of=as_of,
            briefs=briefs,
        )


def _format_user_prompt(
    *,
    venue: str,
    symbol: str,
    timeframe: str,
    as_of: datetime,
    briefs: list[AnalystBrief],
    user_question: str | None,
) -> str:
    parts: list[str] = [
        f"asset: {symbol} @ {venue}",
        f"timeframe: {timeframe}",
        f"as_of: {as_of.isoformat()}",
        "",
        "analyst_briefs:",
    ]
    for b in briefs:
        kp = "\n    - ".join(b.key_points) if b.key_points else "(no key points)"
        parts.append(
            f"  [{b.analyst}] stance={b.stance} confidence={b.confidence:.2f}\n"
            f"    summary: {b.summary}\n"
            f"    key_points:\n    - {kp}"
        )
    if user_question:
        parts.append("")
        parts.append(f"user_original_question: {user_question}")
    parts.append("")
    parts.append("Output the required JSON only.")
    return "\n".join(parts)


def _build_plan(
    *,
    raw: dict[str, Any],
    venue: str,
    symbol: str,
    timeframe: str,
    as_of: datetime,
    briefs: list[AnalystBrief],
) -> ResearchPlan:
    """LLM JSON → ResearchPlan，缺字段兜底。"""
    rating = str(raw.get("rating", "neutral")).lower()
    if rating not in ("overweight", "neutral", "underweight"):
        rating = "neutral"

    horizon = str(raw.get("horizon", "swing")).lower()
    if horizon not in ("intraday", "swing", "position"):
        horizon = "swing"

    payload = {
        "venue": venue,
        "symbol": symbol,
        "timeframe": timeframe,
        "as_of": as_of,
        "rating": rating,
        "confidence": float(raw.get("confidence", 0.5)),
        "thesis": str(raw.get("thesis", "")).strip() or "(no thesis)",
        "risks": [str(r) for r in (raw.get("risks") or [])],
        "suggested_action": str(raw.get("suggested_action", "wait")).strip() or "wait",
        "briefs": briefs,
        "horizon": horizon,
    }
    # 用 model_validate 而不是构造器，让 Pydantic 把 dict→model 一次校验
    return ResearchPlan.model_validate(payload)


# 给测试用：直接走 _build_plan 不调 LLM
def build_plan_from_raw(
    raw: dict[str, Any],
    *,
    venue: str,
    symbol: str,
    timeframe: str,
    as_of: datetime,
    briefs: list[AnalystBrief],
) -> ResearchPlan:
    """测试 entrypoint。生产代码走 ``ResearchManager.synthesize``。"""
    return _build_plan(
        raw=raw,
        venue=venue,
        symbol=symbol,
        timeframe=timeframe,
        as_of=as_of,
        briefs=briefs,
    )


def briefs_to_compact_text(briefs: list[AnalystBrief]) -> str:
    """生成给 LLM 用的紧凑文本（telemetry / 日志也用）。"""
    return json.dumps(
        [b.model_dump(mode="json") for b in briefs],
        ensure_ascii=False,
        indent=2,
    )
