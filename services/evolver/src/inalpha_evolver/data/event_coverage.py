"""Conservative necessary-input bounds; never a replacement for candidate evaluation."""

from datetime import UTC, datetime
from typing import Any

from inalpha_paper.model.market_events import MarketEvent

from .manifest import FrozenDataset


def inspect_event_coverage(
    dataset: FrozenDataset,
    events: list[MarketEvent],
    asset: str,
) -> dict[str, Any]:
    """Bound possible selection evidence without reading returns or sealed holdout outcomes.

    Same-type 24-hour-separated events are counted greedily from the earliest time,
    which maximizes the number of eligible intervals. Candidate severity/confidence,
    holding periods and matching can only reduce this upper bound. Counting all
    types is deliberately permissive; this cannot certify any actual candidate.
    """
    bars = dataset.bars
    discovery_end = max(2, int(len(bars) * 0.60))
    selection_end = min(max(discovery_end + 2, int(len(bars) * 0.80)), len(bars) - 1)
    selection = bars[discovery_end:selection_end]
    start = selection[0].bar_known_at if selection else None
    end = selection[-1].bar_known_at if selection else None
    selected = [
        event
        for event in events
        if start is not None
        and end is not None
        and start <= event.available_at <= end
        and (not event.assets or asset.upper() in event.assets)
    ]
    last_seen: dict[str, int] = {}
    counts: dict[str, int] = {}
    day = 24 * 3600 * 10**9
    for event in sorted(selected, key=lambda value: (value.available_at, value.event_id)):
        previous = last_seen.get(event.event_type)
        if previous is None or event.available_at - previous >= day:
            last_seen[event.event_type] = event.available_at
            counts[event.event_type] = counts.get(event.event_type, 0) + 1
    upper_bound = sum(counts.values())
    # matched-events-v1 requires at least eight pairs; no candidate can pair more
    # events than this deliberately permissive bound on the selection input.
    blocked = upper_bound < 8
    return {
        "status": "blocked" if blocked else "necessary_inputs_present",
        "blocker_codes": ["E2_INPUT_COVERAGE_INSUFFICIENT"] if blocked else [],
        "dataset_content_sha256": dataset.manifest.content_sha256,
        "bar_count": len(bars),
        "selection_start": datetime.fromtimestamp(start / 1e9, UTC).isoformat() if start else None,
        "selection_end": datetime.fromtimestamp(end / 1e9, UTC).isoformat() if end else None,
        "selection_fact_count": len(selected),
        "independent_event_upper_bound": upper_bound,
        "independent_upper_bound_by_type": counts,
        "minimum_matched_event_pairs": 8,
        "matched_controls": "not_evaluated",
        "fdr": "not_evaluated",
        "holdout": "not_evaluated",
        "next_action": "collect_real_evidence_for_a_new_explicit_experiment"
        if blocked
        else "evaluate_candidates",
    }
