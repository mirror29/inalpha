"""Deterministic direction-coverage scaffold before owner Agent proposals."""

from __future__ import annotations

from typing import Any

from .models import HypothesisSpec


def seed_generation_one(snapshot: dict[str, Any], asset: str) -> list[HypothesisSpec]:
    """Create eight diverse, falsifiable slots grounded only in frozen fact IDs."""
    facts = [item for item in snapshot.get("facts", []) if isinstance(item, dict)]
    by_type: dict[str, list[dict[str, Any]]] = {}
    for fact in facts:
        by_type.setdefault(str(fact.get("event_type") or "other"), []).append(fact)
    ranked_types = sorted(
        by_type,
        key=lambda event_type: (
            -max(float(item.get("severity") or 0) for item in by_type[event_type]),
            event_type,
        ),
    )
    while len(ranked_types) < 3:
        ranked_types.append(("listing", "exploit", "chain_halt")[len(ranked_types)])
    event_types = ranked_types[:3]
    seeds: list[HypothesisSpec] = []
    for index, event_type in enumerate(event_types):
        facts_for_type = by_type.get(event_type, [])
        direction = (
            "short" if event_type in {"delisting", "exploit", "chain_halt", "unlock"} else "long"
        )
        seeds.append(
            HypothesisSpec(
                lane="event",
                thesis=f"冻结事实显示 {event_type} 可能产生可证伪的延迟反应，需以事后成本收益和匹配对照验证。",
                evidence_ids=_evidence_ids(facts_for_type),
                event_types=[event_type],
                assets=[asset],
                direction=direction,
                trigger_mode=(
                    "direct"
                    if index == 0
                    and event_type in {"listing", "delisting", "exploit", "chain_halt"}
                    else "confirmed"
                ),
            )
        )
    seeds.extend(
        [
            HypothesisSpec(
                lane="event_regime",
                thesis="事件冲击只在高成交量或高波动状态持续，状态过滤应提高事件对照优势。",
                evidence_ids=_evidence_ids(facts),
                event_types=[event_types[0]],
                assets=[asset],
                applicable_regimes=["high_volume", "high_volatility"],
                direction=seeds[0].direction,
                trigger_mode="hybrid",
            ),
            HypothesisSpec(
                lane="factor",
                thesis="少量动量和成交量确认因子可作为事件后的证伪条件，而不是独立产生方向。",
                event_types=["other"],
                assets=[asset],
                direction="long",
                trigger_mode="confirmed",
            ),
            HypothesisSpec(
                lane="execution_risk",
                thesis="降低事件期仓位并缩短持有期可能在保留异常收益的同时减少不利滑点与回撤。",
                event_types=["other"],
                assets=[asset],
                direction="long",
                trigger_mode="confirmed",
                risk={"position_pct": 0.05},
                invalidation={"ttl_bars": 4, "holding_bars": 6, "max_adverse_pct": 2.0},
            ),
            HypothesisSpec(
                lane="regime",
                thesis="市场状态迁移本身可形成方向，但必须通过封闭验证集证明不依赖单一事件类别。",
                event_types=["other"],
                assets=[asset],
                applicable_regimes=["trend_transition"],
                direction="long",
                trigger_mode="confirmed",
            ),
            HypothesisSpec(
                lane="restart",
                lineage_kind="restart",
                thesis="自由重启探索安全事件后的反转机制，并与当前假设档案保持明显语义距离。",
                evidence_ids=_evidence_ids(facts),
                event_types=["exploit"],
                assets=[asset],
                direction="long",
                trigger_mode="confirmed",
            ),
        ]
    )
    return seeds


def _evidence_ids(facts: list[dict[str, Any]]) -> list[str]:
    return [f"{item['fact_id']}:0" for item in facts[:16] if item.get("fact_id")]


__all__ = ["seed_generation_one"]
