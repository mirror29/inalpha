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


def test_preflight_taxonomy_matches_formal_hypothesis_contract():
    from inalpha_evolver.hypothesis.models import _DIRECT_ALLOWED, _EVENT_TYPES

    module = runpy.run_path(str(Path(__file__).parents[1] / "check-e2-readiness.py"))
    assert set(module["SUPPORTED_EVENT_TYPES"]) == _EVENT_TYPES
    assert module["DIRECT_EVENT_TYPES"] == _DIRECT_ALLOWED


def test_confirmed_macro_events_are_counted_without_becoming_direct_events():
    module = runpy.run_path(str(Path(__file__).parents[1] / "check-e2-readiness.py"))
    start = datetime(2026, 9, 30, tzinfo=UTC)
    facts = [
        {"event_type": "macro", "available_at": start + timedelta(hours=hour)}
        for hour in (0, 2)
    ]
    result = module["summarize_selection"](facts)
    assert result["independent_events_by_type"] == [
        {"event_type": "macro", "independent_events": 1}
    ]
    assert result["direct_independent_events_by_type"] == []
    assert result["coverage_sufficient"] is False
    assert result["minimum_matched_event_pairs"] == 8


def test_eight_independent_confirmed_events_satisfy_necessary_coverage_only():
    module = runpy.run_path(str(Path(__file__).parents[1] / "check-e2-readiness.py"))
    start = datetime(2026, 9, 30, tzinfo=UTC)
    facts = [
        {"event_type": "regulatory", "available_at": start + timedelta(days=day)}
        for day in range(8)
    ]
    result = module["summarize_selection"](facts)
    assert result["coverage_sufficient"] is True
    assert result["direct_independent_events_by_type"] == []


def test_catch_all_events_are_diagnostic_only_even_with_sufficient_count():
    module = runpy.run_path(str(Path(__file__).parents[1] / "check-e2-readiness.py"))
    start = datetime(2026, 9, 30, tzinfo=UTC)
    facts = [
        {"event_type": "other", "available_at": start + timedelta(days=day)}
        for day in range(8)
    ]
    result = module["summarize_selection"](facts)
    assert result["independent_events_by_type"][0]["independent_events"] == 8
    assert result["qualifying_independent_events_by_type"] == []
    assert result["coverage_sufficient"] is False


def test_catch_all_cannot_top_up_seven_typed_independent_events():
    module = runpy.run_path(str(Path(__file__).parents[1] / "check-e2-readiness.py"))
    start = datetime(2026, 9, 30, tzinfo=UTC)
    facts = [
        {"event_type": "macro", "available_at": start + timedelta(days=day)}
        for day in range(7)
    ] + [{"event_type": "other", "available_at": start}]
    result = module["summarize_selection"](facts)
    assert result["qualifying_independent_events_by_type"] == [
        {"event_type": "macro", "independent_events": 7}
    ]
    assert result["coverage_sufficient"] is False
