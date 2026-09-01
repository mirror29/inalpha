"""Deterministic event reaction and matched no-event control metrics."""

from __future__ import annotations

import bisect
import statistics
from dataclasses import dataclass
from itertools import pairwise
from typing import Any

from inalpha_paper.model.data import Bar
from inalpha_paper.model.market_events import MarketEvent


@dataclass(frozen=True, slots=True)
class EventStudyResult:
    """Aggregate event-window evidence safe to expose to upper-level selection."""

    event_count: int
    matched_control_count: int
    mean_event_return_pct: float
    mean_control_return_pct: float
    event_advantage_pct: float
    positive_event_ratio: float
    mean_mfe_pct: float
    mean_mae_pct: float
    unmatched_events: int
    event_effects: tuple[float, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "event_count": self.event_count,
            "matched_control_count": self.matched_control_count,
            "mean_event_return_pct": self.mean_event_return_pct,
            "mean_control_return_pct": self.mean_control_return_pct,
            "event_advantage_pct": self.event_advantage_pct,
            "positive_event_ratio": self.positive_event_ratio,
            "mean_mfe_pct": self.mean_mfe_pct,
            "mean_mae_pct": self.mean_mae_pct,
            "unmatched_events": self.unmatched_events,
            "event_effects": list(self.event_effects),
        }


def evaluate_event_reactions(
    *,
    bars: list[Bar],
    events: list[MarketEvent],
    asset: str,
    direction: str,
    holding_bars: int,
    exclusion_bars: int,
    volatility_tolerance: float,
    volume_tolerance: float,
) -> EventStudyResult:
    """Compare event windows with nearest pre-event regime/volume controls."""
    if len(bars) < holding_bars + 3:
        return _empty(len(events))
    times = [bar.bar_known_at for bar in bars]
    event_indices: list[int] = []
    for event in _independent_events(events, asset):
        if event.assets and asset.upper() not in event.assets:
            continue
        index = bisect.bisect_left(times, event.available_at)
        if 1 <= index < len(bars) - holding_bars:
            event_indices.append(index)
    excluded = {
        index
        for event_index in event_indices
        for index in range(
            max(1, event_index - exclusion_bars),
            min(len(bars) - holding_bars, event_index + exclusion_bars + 1),
        )
    }
    event_returns: list[float] = []
    control_returns: list[float] = []
    mfes: list[float] = []
    maes: list[float] = []
    paired_effects: list[float] = []
    unmatched = 0
    for event_index in event_indices:
        event_return, mfe, mae = _window_metrics(bars, event_index, holding_bars, direction)
        event_returns.append(event_return)
        mfes.append(mfe)
        maes.append(mae)
        control = _match_control(
            bars,
            event_index,
            holding_bars=holding_bars,
            excluded=excluded,
            volatility_tolerance=volatility_tolerance,
            volume_tolerance=volume_tolerance,
        )
        if control is None:
            unmatched += 1
        else:
            control_return = _window_metrics(bars, control, holding_bars, direction)[0]
            control_returns.append(control_return)
            paired_effects.append(event_return - control_return)
    event_mean = statistics.mean(event_returns) if event_returns else 0.0
    control_mean = statistics.mean(control_returns) if control_returns else 0.0
    return EventStudyResult(
        event_count=len(event_returns),
        matched_control_count=len(control_returns),
        mean_event_return_pct=event_mean,
        mean_control_return_pct=control_mean,
        event_advantage_pct=event_mean - control_mean,
        positive_event_ratio=(
            sum(value > 0 for value in event_returns) / len(event_returns) if event_returns else 0.0
        ),
        mean_mfe_pct=statistics.mean(mfes) if mfes else 0.0,
        mean_mae_pct=statistics.mean(maes) if maes else 0.0,
        unmatched_events=unmatched,
        event_effects=tuple(paired_effects),
    )


def _independent_events(events: list[MarketEvent], asset: str) -> list[MarketEvent]:
    """Cluster same-asset/type messages within 24 hours into one independent event."""
    cluster_ns = 24 * 60 * 60 * 1_000_000_000
    last_seen: dict[tuple[str, str], int] = {}
    independent: list[MarketEvent] = []
    for event in sorted(events, key=lambda item: (item.available_at, item.event_id)):
        assets = event.assets or (asset.upper(),)
        relevant = tuple(item for item in assets if item == asset.upper())
        if not relevant:
            continue
        key = (relevant[0], event.event_type)
        previous = last_seen.get(key)
        if previous is not None and event.available_at - previous < cluster_ns:
            continue
        last_seen[key] = event.available_at
        independent.append(event)
    return independent


def _match_control(
    bars: list[Bar],
    event_index: int,
    *,
    holding_bars: int,
    excluded: set[int],
    volatility_tolerance: float,
    volume_tolerance: float,
) -> int | None:
    target_volatility, target_volume = _context(bars, event_index)
    best: tuple[float, int] | None = None
    for index in range(20, event_index - holding_bars):
        if index in excluded:
            continue
        volatility, volume = _context(bars, index)
        vol_distance = _relative_distance(volatility, target_volatility)
        volume_distance = _relative_distance(volume, target_volume)
        if vol_distance > volatility_tolerance or volume_distance > volume_tolerance:
            continue
        distance = vol_distance + volume_distance
        if best is None or (distance, index) < best:
            best = (distance, index)
    return best[1] if best else None


def _context(bars: list[Bar], index: int) -> tuple[float, float]:
    window = bars[max(0, index - 20) : index]
    returns = [
        (right.close / left.close) - 1.0 for left, right in pairwise(window) if left.close > 0
    ]
    volatility = statistics.pstdev(returns) if len(returns) > 1 else 0.0
    volume = statistics.mean(bar.volume for bar in window) if window else 0.0
    return volatility, volume


def _window_metrics(
    bars: list[Bar], index: int, holding_bars: int, direction: str
) -> tuple[float, float, float]:
    entry = bars[index].close
    window = bars[index + 1 : index + holding_bars + 1]
    sign = -1.0 if direction == "short" else 1.0
    returns = [sign * ((bar.close / entry) - 1.0) * 100 for bar in window if entry > 0]
    final_return = returns[-1] if returns else 0.0
    return final_return, max(returns, default=0.0), min(returns, default=0.0)


def _relative_distance(left: float, right: float) -> float:
    denominator = max(abs(right), 1e-12)
    return abs(left - right) / denominator


def _empty(unmatched_events: int) -> EventStudyResult:
    return EventStudyResult(0, 0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, unmatched_events, ())


__all__ = ["EventStudyResult", "evaluate_event_reactions"]
