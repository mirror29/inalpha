from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

from inalpha_paper.evolution_forward_manager import _persisted_fills
from inalpha_paper.forward_evidence import decide_forward_status, independent_facts


def _fact(at: datetime, *, event_type: str = "listing") -> dict:
    return {
        "fact_id": uuid4(),
        "event_type": event_type,
        "asset_ids": ["asset:BTC"],
        "available_at": at,
    }


def test_independent_facts_clusters_correlated_messages() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    facts = [_fact(start), _fact(start + timedelta(hours=23)), _fact(start + timedelta(hours=25))]

    assert [item["available_at"] for item in independent_facts(facts)] == [
        start,
        start + timedelta(hours=25),
    ]


def test_forward_gate_requires_duration_events_and_two_thirds_positive() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    metrics = {
        "total_return_pct": 2.0,
        "positive_event_count": 2,
        "event_advantage_pct": 0.4,
    }

    assert (
        decide_forward_status(
            started_at=start,
            deadline_at=start + timedelta(days=90),
            now=start + timedelta(days=29),
            event_count=3,
            metrics=metrics,
            risk_alerts=[],
            data_quality_alerts=[],
        )
        == "observing"
    )
    assert (
        decide_forward_status(
            started_at=start,
            deadline_at=start + timedelta(days=90),
            now=start + timedelta(days=30),
            event_count=3,
            metrics=metrics,
            risk_alerts=[],
            data_quality_alerts=[],
        )
        == "passed"
    )


def test_forward_gate_marks_90_day_event_shortfall() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)

    assert (
        decide_forward_status(
            started_at=start,
            deadline_at=start + timedelta(days=90),
            now=start + timedelta(days=90),
            event_count=2,
            metrics=None,
            risk_alerts=[],
            data_quality_alerts=[],
        )
        == "insufficient_evidence"
    )


def test_persisted_fills_only_attribute_recent_events() -> None:
    filled_at = datetime(2026, 1, 3, tzinfo=UTC)
    fill = SimpleNamespace(
        ts_ns=int(filled_at.timestamp() * 1_000_000_000),
        side="buy",
        quantity=1.0,
        fill_price=101.0,
        fee=0.1,
    )
    bar = SimpleNamespace(
        bar_known_at=fill.ts_ns,
        open=100.0,
    )
    stale = _fact(filled_at - timedelta(hours=25))
    recent = _fact(filled_at - timedelta(hours=23))

    persisted = _persisted_fills([fill], [bar], [stale, recent])

    assert persisted[0]["fact_id"] == str(recent["fact_id"])


def test_persisted_fills_do_not_attribute_stale_events() -> None:
    filled_at = datetime(2026, 1, 3, tzinfo=UTC)
    fill = SimpleNamespace(
        ts_ns=int(filled_at.timestamp() * 1_000_000_000),
        side="buy",
        quantity=1.0,
        fill_price=101.0,
        fee=0.1,
    )
    bar = SimpleNamespace(bar_known_at=fill.ts_ns, open=100.0)

    persisted = _persisted_fills(
        [fill],
        [bar],
        [_fact(filled_at - timedelta(hours=25))],
    )

    assert persisted[0]["fact_id"] is None
