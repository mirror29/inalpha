"""Deterministic evidence and terminal gates for isolated evolution Forward sandboxes."""

from __future__ import annotations

import math
from datetime import timedelta
from typing import Any


def independent_facts(facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Cluster same-type, same-asset facts arriving within 24 hours into one event."""
    selected: list[dict[str, Any]] = []
    last_by_key: dict[tuple[str, tuple[str, ...]], Any] = {}
    for fact in sorted(facts, key=lambda item: (item["available_at"], str(item["fact_id"]))):
        key = (str(fact["event_type"]), tuple(sorted(fact.get("asset_ids") or ())))
        previous = last_by_key.get(key)
        if previous is not None and fact["available_at"] - previous < timedelta(hours=24):
            continue
        selected.append(fact)
        last_by_key[key] = fact["available_at"]
    return selected


def event_reaction_metrics(
    *,
    bars: list[Any],
    facts: list[dict[str, Any]],
    direction: str,
    holding_bars: int,
    total_cost_pct: float,
) -> dict[str, Any]:
    """Measure post-cost reactions and matched prior-window controls from persisted inputs."""
    sign = -1.0 if direction == "short" else 1.0
    effects: list[float] = []
    advantages: list[float] = []
    positive = 0
    matched = 0
    per_event_cost = total_cost_pct / max(1, len(facts))
    for fact in independent_facts(facts):
        available_ns = int(fact["available_at"].timestamp() * 1_000_000_000)
        entry_index = next(
            (index for index, bar in enumerate(bars) if bar.bar_known_at >= available_ns),
            None,
        )
        if entry_index is None or entry_index + holding_bars >= len(bars):
            continue
        entry = bars[entry_index].close
        exit_price = bars[entry_index + holding_bars].close
        effect = sign * (exit_price / entry - 1.0) * 100.0 - per_event_cost
        effects.append(effect)
        if effect > 0:
            positive += 1
        if entry_index >= holding_bars:
            control_entry = bars[entry_index - holding_bars].close
            control = sign * (entry / control_entry - 1.0) * 100.0
            advantages.append(effect - control)
            matched += 1
    return {
        "independent_event_count": len(independent_facts(facts)),
        "measured_event_count": len(effects),
        "positive_event_count": positive,
        "matched_control_count": matched,
        "match_ratio": matched / max(1, len(effects)),
        "mean_post_cost_return_pct": sum(effects) / len(effects) if effects else None,
        "event_advantage_pct": sum(advantages) / len(advantages) if advantages else None,
        "event_effects": effects,
        "false_positive_count": len(effects) - positive,
        "missed_event_count": max(0, len(independent_facts(facts)) - len(effects)),
    }


def decide_forward_status(
    *,
    started_at: Any,
    deadline_at: Any,
    now: Any,
    event_count: int,
    metrics: dict[str, Any] | None,
    risk_alerts: list[str],
    data_quality_alerts: list[str],
) -> str:
    """Apply the frozen 30-day/three-event/90-day terminal gate."""
    if now >= deadline_at and event_count < 3:
        return "insufficient_evidence"
    if now < started_at + timedelta(days=30) or event_count < 3 or metrics is None:
        return "observing"
    positive = int(metrics.get("positive_event_count") or 0)
    required_positive = math.ceil(event_count * 2 / 3)
    passed = (
        float(metrics.get("total_return_pct") or 0.0) > 0
        and positive >= required_positive
        and float(metrics.get("event_advantage_pct") or 0.0) > 0
        and not risk_alerts
        and not data_quality_alerts
    )
    return "passed" if passed else "failed"


__all__ = ["decide_forward_status", "event_reaction_metrics", "independent_facts"]
