"""Repeated news cannot satisfy the independent-event search preflight."""

import runpy
from datetime import UTC, datetime, timedelta
from pathlib import Path

independent_counts = runpy.run_path(
    str(Path(__file__).parents[1] / "check-e2-readiness.py")
)["independent_counts"]


def test_eight_same_day_articles_are_one_independent_event():
    start = datetime(2026, 9, 30, tzinfo=UTC)
    facts = [
        {"event_type": "exploit", "available_at": start + timedelta(hours=index)}
        for index in range(8)
    ]
    assert independent_counts(facts) == [
        {"event_type": "exploit", "independent_events": 1}
    ]


def test_independence_uses_cluster_start_and_exact_24_hour_boundary():
    start = datetime(2026, 9, 30, tzinfo=UTC)
    facts = [
        {"event_type": "exploit", "available_at": start + timedelta(hours=hours)}
        for hours in (24, 23, 0)
    ]
    assert independent_counts(facts) == [
        {"event_type": "exploit", "independent_events": 2}
    ]
