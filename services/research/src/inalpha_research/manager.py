"""Research Manager —— 综合 analyst briefs 输出最终 ``ResearchPlan``。

设计要点：

- manager 自己也是 LLM call，但 system prompt **只让它综合** —— 不允许它绕过
  analyst 自己判断（防"双 LLM 互相同意"风险，[ADR-0012 Alt D](../../../docs/miro/decisions/0012-plan-exec-separation.md)）
- 容错：LLM 返回的字段缺失 / 不符 schema 时用默认值兜底，不抛错（避免一次 LLM
  抽风就让整条 deep_dive 链路 500）
- D-8c 起：manager 额外产出 ``factors`` / ``signals`` / ``strategy_hint`` 三段
  结构化字段，作为 research → strategy 机器路径的契约。LLM 没给齐时由
  ``_derive_strategy_hint`` 从 analyst briefs 兜底推断。
"""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime
from typing import Any

from inalpha_shared import get_logger

from .llm.client import LLMClient, LLMError
from .schemas import (
    AnalystBrief,
    DebateTurn,
    Factor,
    ResearchPlan,
    Signal,
    StrategyFamily,
    StrategyHint,
)

_logger = get_logger(__name__)

# manager 综合输出比单个 analyst 大得多（thesis + risks + factors + signals +
# strategy_hint，且要消化 6-8 个 briefs + 辩论）。默认 2048 容易截断 → 残缺 JSON →
# LLMError（历史上的 deep_dive 502 根因，ADR-0037 调试记录）。给足余量。
_MANAGER_MAX_TOKENS = 4096

_SYSTEM = """
You are a research manager + debate judge synthesizing analyst briefs and a
Bull/Bear debate into a final research plan.

You receive 1+ analyst briefs. Each brief has stance / confidence / summary /
key_points and may include structured "factors" (kind / value / strength / explanation).
You may also receive a debate_log (Bull/Bear turns, possibly with a third "risk"
voice stress-testing both sides) — if present, the researchers have already argued
over the same briefs; weight the side whose argument better withstood the rebuttals,
and treat the risk officer's unanswered challenges as live risks.

Your job is to:
1. Reconcile disagreements between analysts (favor the one with more concrete evidence)
2. Judge the debate (when present) — note which side conceded points or
   handled rebuttals better; reflect that in rating + confidence. Risk-officer
   challenges that neither side answered belong in "risks"
3. Output a final rating, thesis, risks, and a suggested action for the trader
4. **Synthesize factors and pick a strategy family** —— machine-readable hand-off to the
   downstream `paper.compose_strategy` engine
5. Stay **grounded in the briefs / debate** — do not invent factors that no analyst raised
6. **Show your weighing** in "reasoning": which analysts you trusted and why, who won
   the debate and on what point — this is the audit trail for "why this rating"

Return ONLY a JSON object with this exact shape:

{
  "rating": "overweight" | "neutral" | "underweight",
  "confidence": float in [0, 1],
  "thesis": "3-5 sentences of core conclusion",
  "reasoning": "2-4 sentences: how you weighed the analysts and judged the debate",
  "risks": ["risk 1", "risk 2", ...],
  "suggested_action": "open_long 0.X | open_short 0.X | hold | reduce | wait",
  "horizon": "intraday" | "swing" | "position",

  "factors": [                                  // dedup + merge of analyst factors (3-6)
    {
      "name": "rsi_14_neutral",
      "kind": "momentum" | "mean_reversion" | "volatility" | "macro" | "sentiment",
      "value": 58.2,
      "strength": 0.5,
      "horizon": "swing",
      "explanation": "RSI 58 - room to upside, not overbought"
    }
  ],

  "signals": [                                  // 1-3 directional signals from factors
    {
      "direction": "long" | "short" | "flat",
      "strength": 0.6,
      "timeframe": "1h" | "4h" | "1d",
      "derived_from": ["factor.name", ...]
    }
  ],

  "strategy_hint": {
    "family": "trend" | "mean_reversion" | "buy_hold" | "none",
    "params": {                                 // recommended starting params (compose engine may tighten)
      "fast_period": 10,
      "slow_period": 30,
      "trade_size": 0.02
    },
    "reasoning": "1-2 sentence why this family fits the factor mix"
  }
}

Rules for strategy_hint (pick the family that best matches the DOMINANT factor mix —
do NOT default to "trend"; diversify based on what the factors actually say):
- "trend"          — momentum/MA factors dominate with a steady directional drift (SMA cross, MACD)
- "mean_reversion" — oscillator extremes + low recent trend (RSI extreme, BB %B at edges)
- "breakout"       — price compressing then a clear range break / new N-bar high (Donchian channel)
- "volatility"     — volatility regime expanding (ATR rising); want channel-breakout sizing (ATR/Keltner)
- "buy_hold"       — strong long-horizon macro thesis + no near-term technical edge
- "none"           — factors disagree too much, or rating "neutral" with low confidence

Rules for params (pick concrete numbers, not ranges; trade_size scales with confidence × signal strength):
- trend          : { fast_period: 5-20, slow_period: 20-60, trade_size: 0.01-0.05 }
- mean_reversion : { period: 10-30, num_std: 1.5-2.5, trade_size: 0.01-0.05 }
- breakout       : { channel_period: 10-55, exit_period: 5-30, trade_size: 0.01-0.05 }
- volatility     : { period: 10-40, atr_mult: 1.0-4.0, trade_size: 0.01-0.05 }
- buy_hold       : { trade_size: 0.5-1.0 (fraction of cash) }

Reconciliation rules:
- If analysts disagree strongly, prefer "neutral" + low confidence + strategy_hint.family == "none"
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
        debate_log: list[DebateTurn] | None = None,
        user_question: str | None = None,
        debate_trigger: str | None = None,
        debate_stop_reason: str | None = None,
    ) -> ResearchPlan:
        """综合 briefs(+debate) 出 plan。

        ``debate_trigger`` / ``debate_stop_reason`` 由 runner 传入、原样落进
        plan（research-hub #6 决策链路可观测）——manager 不消费它们。
        """
        user_prompt = _format_user_prompt(
            venue=venue,
            symbol=symbol,
            timeframe=timeframe,
            as_of=as_of,
            briefs=briefs,
            debate_log=debate_log or [],
            user_question=user_question,
        )
        try:
            raw = await self._llm.complete_json(
                system=_SYSTEM, user=user_prompt, max_tokens=_MANAGER_MAX_TOKENS
            )
        except LLMError as e:
            # manager 综合失败不应丢掉 analyst 成果——对齐 analyst ``_failed_brief`` /
            # debate ``_safe_speak`` 的容错哲学：降级返 neutral plan（带上 briefs /
            # debate_log），而不是让整条 deep_dive 抛 502。
            _logger.warning("manager_synthesis_failed", symbol=symbol, error=repr(e))
            raw = _fallback_raw(repr(e))
        return _build_plan(
            raw=raw,
            venue=venue,
            symbol=symbol,
            timeframe=timeframe,
            as_of=as_of,
            briefs=briefs,
            debate_log=debate_log or [],
            debate_trigger=debate_trigger,
            debate_stop_reason=debate_stop_reason,
        )


def _clamp_unit(v: Any, default: float = 0.5) -> float:
    """confidence clamp 到 [0, 1]；非数值兜底 default。D-8b' review B2 fix。"""
    try:
        x = float(v) if v is not None else default
    except (TypeError, ValueError):
        return default
    if x < 0.0:
        return 0.0
    if x > 1.0:
        return 1.0
    return x


def _format_user_prompt(
    *,
    venue: str,
    symbol: str,
    timeframe: str,
    as_of: datetime,
    briefs: list[AnalystBrief],
    debate_log: list[DebateTurn],
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
        factors_block = ""
        if b.factors:
            factor_lines = [
                f"      - {f.name} ({f.kind}, strength={f.strength:.2f}): {f.value}"
                for f in b.factors
            ]
            factors_block = "\n    factors:\n" + "\n".join(factor_lines)
        parts.append(
            f"  [{b.analyst}] stance={b.stance} confidence={b.confidence:.2f}\n"
            f"    summary: {b.summary}\n"
            f"    key_points:\n    - {kp}"
            f"{factors_block}"
        )

    if debate_log:
        parts.append("")
        parts.append("debate_log (Bull/Bear arguments, oldest first):")
        for turn in debate_log:
            parts.append(f"  Round {turn.round} {turn.role.upper()}: {turn.content}")
    else:
        parts.append("")
        parts.append("debate_log: (no debate this run — judge briefs only)")

    if user_question:
        parts.append("")
        parts.append(f"user_original_question: {user_question}")
    parts.append("")
    parts.append("Output the required JSON only.")
    return "\n".join(parts)


def _fallback_raw(error: str) -> dict[str, Any]:
    """manager LLM 调用失败时的降级 raw —— 喂给 ``_build_plan`` 产出 neutral plan。

    ``_merge_factors`` 会从 briefs 兜底因子，``_parse_strategy_hint`` 会从 rating /
    factors 兜底推断，因此即便综合失败，下游仍拿到一个可用（虽保守）的 plan + 全部
    analyst briefs，而不是 502 丢掉一切。
    """
    # thesis / risks 是面向用户的字段。这里用**英文**占位与 manager 英文 system prompt
    # 一致（CLAUDE.md §3：禁在 prompt/输出里写死中英文）；面向用户的语言由 orchestrator
    # 按用户最近一条消息的语言翻译呈现，不在此层固定中文。
    return {
        "rating": "neutral",
        "confidence": 0.0,
        "thesis": (
            "Synthesis unavailable: the manager LLM call failed; this result is based "
            "solely on the individual analyst briefs below — judge accordingly. "
            f"detail: {error[:200]}"
        ),
        "reasoning": (
            "no weighing performed: the synthesis LLM call failed, so neither analyst "
            "reconciliation nor debate adjudication happened"
        ),
        "risks": [
            "Manager synthesis failed — no cross-analyst reconciliation / debate "
            "adjudication was performed"
        ],
        "suggested_action": "wait",
        # 显式空 factors：阻止 _merge_factors 把 briefs 的 factor 全拉进来。否则
        # persona/valuation 的 kind=macro factor 会让 _dominant_kind 多数投票偏向 macro
        # → strategy 兜底成 buy_hold（综合本就失败，不该再据此推策略族）。
        "factors": [],
    }


def _build_plan(
    *,
    raw: dict[str, Any],
    venue: str,
    symbol: str,
    timeframe: str,
    as_of: datetime,
    briefs: list[AnalystBrief],
    debate_log: list[DebateTurn] | None = None,
    debate_trigger: str | None = None,
    debate_stop_reason: str | None = None,
) -> ResearchPlan:
    """LLM JSON → ResearchPlan，缺字段兜底。``debate_log`` 原样落进 plan 字段。"""
    rating = str(raw.get("rating", "neutral")).lower()
    if rating not in ("overweight", "neutral", "underweight"):
        rating = "neutral"

    horizon = str(raw.get("horizon", "swing")).lower()
    if horizon not in ("intraday", "swing", "position"):
        horizon = "swing"

    # D-8b' review B2 fix: confidence clamp 到 [0, 1]，避免 LLM 抽风返 1.5
    # 让 pydantic 抛 → 整条 deep_dive 500（manager 声称"兜底不抛"打脸）
    confidence = _clamp_unit(raw.get("confidence"))

    factors = _merge_factors(raw.get("factors"), briefs)
    signals = _parse_signals(raw.get("signals"))
    strategy_hint = _parse_strategy_hint(
        raw.get("strategy_hint"),
        rating=rating,
        confidence=confidence,
        factors=factors,
    )

    payload = {
        "venue": venue,
        "symbol": symbol,
        "timeframe": timeframe,
        "as_of": as_of,
        "rating": rating,
        "confidence": confidence,
        "thesis": str(raw.get("thesis", "")).strip() or "(no thesis)",
        "risks": [str(r) for r in (raw.get("risks") or [])],
        "suggested_action": str(raw.get("suggested_action", "wait")).strip() or "wait",
        "factors": [f.model_dump(mode="json") for f in factors],
        "signals": [s.model_dump(mode="json") for s in signals],
        "strategy_hint": strategy_hint.model_dump(mode="json"),
        "briefs": briefs,
        "debate_log": [t.model_dump(mode="json") for t in (debate_log or [])],
        "horizon": horizon,
        # research-hub #6 决策链路：trigger/stop 来自 runner，reasoning 来自 LLM 自述
        "debate_trigger": debate_trigger,
        "debate_stop_reason": debate_stop_reason,
        "synthesis_reasoning": str(raw.get("reasoning") or "").strip() or None,
    }
    # 用 model_validate 而不是构造器，让 Pydantic 把 dict→model 一次校验
    return ResearchPlan.model_validate(payload)


# ────────────────────────────────────────────────────────────────────
# 兜底 helpers —— LLM 不输出 / 输出残缺时 fallback
# ────────────────────────────────────────────────────────────────────


def _merge_factors(
    raw_factors: Any,
    briefs: list[AnalystBrief],
) -> list[Factor]:
    """factors 是 list（含空 []）即权威；只有 None / 缺字段才从 briefs 兜底。

    关键：之前空列表也会触发 briefs 兜底——manager 失败的 _fallback_raw 走 _build_plan
    时，会把 persona/valuation 的 kind=macro factor 全拉进来，让 _dominant_kind 多数投票
    偏向 macro → strategy 兜底成 buy_hold（综合本就失败，不该再据此推策略族）。
    改为：显式提供 list 就用它（即便空），不再回落 briefs。
    """
    if isinstance(raw_factors, list):
        merged: list[Factor] = []
        for item in raw_factors:
            if not isinstance(item, dict):
                continue
            try:
                merged.append(Factor.model_validate(item))
            except Exception:
                continue
        return merged[:6]
    # raw_factors 不是 list（None / 缺字段）→ 从 briefs 兜底，保证下游有 factor
    out: list[Factor] = []
    for b in briefs:
        out.extend(b.factors)
    return out[:6]


def _parse_signals(raw_signals: Any) -> list[Signal]:
    out: list[Signal] = []
    if not isinstance(raw_signals, list):
        return out
    for item in raw_signals:
        if not isinstance(item, dict):
            continue
        try:
            out.append(Signal.model_validate(item))
        except Exception:
            continue
    return out[:3]


def _parse_strategy_hint(
    raw_hint: Any,
    *,
    rating: str,
    confidence: float,
    factors: list[Factor],
) -> StrategyHint:
    """LLM 没给 strategy_hint 时由因子大类 + rating 推断兜底。

    规则（保守）：
    - rating == "neutral" 且 confidence < 0.6   → family = none
    - factor 主导类 = momentum                  → trend
    - factor 主导类 = mean_reversion            → mean_reversion
    - factor 主导类 = volatility                → volatility（ATR 通道）
    - factor 主导类 = macro / sentiment         → buy_hold
    - 其它                                       → none
    """
    if isinstance(raw_hint, dict):
        try:
            return StrategyHint.model_validate(raw_hint)
        except Exception:
            pass

    if rating == "neutral" and confidence < 0.6:
        return StrategyHint(
            family="none",
            params={},
            reasoning="neutral rating with low confidence — no strategy suggested",
        )

    dominant = _dominant_kind(factors)
    family: StrategyFamily
    params: dict[str, Any]
    reasoning: str
    if dominant == "momentum":
        family = "trend"
        params = {"fast_period": 10, "slow_period": 30, "trade_size": 0.02}
        reasoning = "fallback: momentum-dominant factors → trend family"
    elif dominant == "mean_reversion":
        family = "mean_reversion"
        params = {"period": 20, "num_std": 2.0, "trade_size": 0.02}
        reasoning = "fallback: mean_reversion-dominant factors → mean_reversion family"
    elif dominant == "volatility":
        family = "volatility"
        params = {"period": 20, "atr_mult": 2.0, "trade_size": 0.02}
        reasoning = "fallback: volatility-dominant factors → volatility (ATR channel) family"
    elif dominant in ("macro", "sentiment"):
        family = "buy_hold"
        params = {"trade_size": 0.5}
        reasoning = f"fallback: {dominant}-dominant factors → buy_hold family"
    else:
        family = "none"
        params = {}
        reasoning = "fallback: no factors / disagreement → none"
    return StrategyHint(family=family, params=params, reasoning=reasoning)


def _dominant_kind(factors: list[Factor]) -> str | None:
    """按 strength 加权的 factor.kind majority vote。"""
    if not factors:
        return None
    weights: Counter[str] = Counter()
    for f in factors:
        weights[f.kind] += f.strength
    if not weights:
        return None
    return weights.most_common(1)[0][0]


# 给测试用：直接走 _build_plan 不调 LLM
def build_plan_from_raw(
    raw: dict[str, Any],
    *,
    venue: str,
    symbol: str,
    timeframe: str,
    as_of: datetime,
    briefs: list[AnalystBrief],
    debate_log: list[DebateTurn] | None = None,
    debate_trigger: str | None = None,
    debate_stop_reason: str | None = None,
) -> ResearchPlan:
    """测试 entrypoint。生产代码走 ``ResearchManager.synthesize``。"""
    return _build_plan(
        raw=raw,
        venue=venue,
        symbol=symbol,
        timeframe=timeframe,
        as_of=as_of,
        briefs=briefs,
        debate_log=debate_log,
        debate_trigger=debate_trigger,
        debate_stop_reason=debate_stop_reason,
    )


def briefs_to_compact_text(briefs: list[AnalystBrief]) -> str:
    """生成给 LLM 用的紧凑文本（telemetry / 日志也用）。"""
    return json.dumps(
        [b.model_dump(mode="json") for b in briefs],
        ensure_ascii=False,
        indent=2,
    )
