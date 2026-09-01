"""Pure point-in-time event contract tests without a database dependency."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from inalpha_data.event_models import EventFactWriteRequest, RawEventIngestRequest
from inalpha_data.storage.events import _advisory_lock_key


def test_event_advisory_lock_key_is_postgresql_safe_and_unambiguous() -> None:
    first = _advisory_lock_key("local-demo", "listing-01")

    assert "\0" not in first
    assert len(first) == 64
    assert first == _advisory_lock_key("local-demo", "listing-01")
    assert _advisory_lock_key("a", "bc") != _advisory_lock_key("ab", "c")


def test_realtime_raw_event_cannot_claim_acceptance_before_first_seen() -> None:
    now = datetime.now(UTC)
    with pytest.raises(ValidationError, match="accepted_at"):
        RawEventIngestRequest(
            source="official-exchange",
            source_event_id="1",
            first_seen_at=now,
            fetched_at=now,
            accepted_at=now - timedelta(seconds=1),
            collector_version="test@1",
            policy_version="first-seen-only-v1",
            source_tier="official",
        )


def test_fact_assets_are_normalized_and_deduplicated() -> None:
    now = datetime.now(UTC)
    fact = EventFactWriteRequest(
        raw_event_id="3b67b111-1dac-4bf4-b70b-ab683c50469d",
        fact_key="listing:btc",
        event_type="listing",
        assets=["btc", " BTC ", "ETH"],
        action="exchange lists BTC",
        severity=0.8,
        confidence=0.9,
        effective_at=now,
        available_at=now,
        extractor_version="test@1",
        policy_version="event-time-policy-v1",
    )
    assert fact.assets == ["BTC", "ETH"]
