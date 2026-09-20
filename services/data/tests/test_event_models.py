"""Pure point-in-time event contract tests without a database dependency."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from inalpha_shared.db import get_conn
from pydantic import ValidationError

from inalpha_data.event_models import (
    EventFactWriteRequest,
    EventSnapshotRequest,
    RawEventIngestRequest,
)
from inalpha_data.storage.events import (
    ALL_VISIBLE_FACTS_POLICY,
    _advisory_lock_key,
    _snapshot_fact_policy_filter,
    claim_extraction_jobs,
    complete_extraction_job,
    create_snapshot,
    ingest_raw_event,
    write_fact,
)


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


def test_all_visible_snapshot_policy_does_not_filter_source_fact_policies() -> None:
    condition, params = _snapshot_fact_policy_filter(ALL_VISIBLE_FACTS_POLICY)
    assert condition == ""
    assert params == []

    condition, params = _snapshot_fact_policy_filter("structured-provider-latency-v1")
    assert condition == "f.policy_version=%s"
    assert params == ["structured-provider-latency-v1"]


@pytest.mark.integration
@pytest.mark.usefixtures("db_pool")
async def test_all_visible_snapshot_retains_facts_from_multiple_source_policies() -> None:
    now = datetime.now(UTC) - timedelta(minutes=1)
    source_suffix = uuid4().hex
    async with get_conn() as conn:
        for index, (source_tier, policy_version) in enumerate(
            [
                ("structured", "structured-provider-latency-v1"),
                ("official", "first-seen-only-v1"),
            ]
        ):
            raw, _ = await ingest_raw_event(
                conn,
                RawEventIngestRequest(
                    source=f"snapshot-policy-{source_suffix}-{index}",
                    source_event_id=f"event-{index}",
                    title=f"BTC event {index}",
                    first_seen_at=now,
                    fetched_at=now,
                    accepted_at=now,
                    collector_version="test@1",
                    policy_version=policy_version,
                    source_tier=source_tier,  # type: ignore[arg-type]
                ),
            )
            await write_fact(
                conn,
                EventFactWriteRequest(
                    raw_event_id=raw["event_id"],
                    fact_key=f"listing:btc:{index}",
                    event_type="listing",
                    assets=["BTC"],
                    action=f"event action {index}",
                    severity=0.8,
                    confidence=0.9,
                    effective_at=now,
                    available_at=now,
                    extractor_version="test@1",
                    policy_version=policy_version,
                ),
            )

        snapshot, facts = await create_snapshot(
            conn,
            EventSnapshotRequest(
                cutoff=now + timedelta(seconds=1),
                policy_version=ALL_VISIBLE_FACTS_POLICY,
                event_types=["listing"],
                assets=["BTC"],
            ),
        )

    assert snapshot["policy_version"] == ALL_VISIBLE_FACTS_POLICY
    assert {fact["policy_version"] for fact in facts} >= {
        "structured-provider-latency-v1",
        "first-seen-only-v1",
    }


@pytest.mark.integration
@pytest.mark.usefixtures("db_pool")
async def test_raw_event_allows_a_b_a_revisions_and_queues_each_version() -> None:
    now = datetime.now(UTC) - timedelta(minutes=5)
    source = f"revision-{uuid4().hex}"

    def request(content: str) -> RawEventIngestRequest:
        return RawEventIngestRequest(
            source=source,
            source_event_id="same-logical-event",
            title="BTC listing",
            content=content,
            first_seen_at=now,
            fetched_at=now,
            accepted_at=now,
            collector_version="test@1",
            policy_version="structured-provider-latency-v1",
            source_tier="structured",
        )

    async with get_conn() as conn:
        first, _ = await ingest_raw_event(conn, request("A"))
        second, _ = await ingest_raw_event(conn, request("B"))
        third, created = await ingest_raw_event(conn, request("A"))
        jobs = await claim_extraction_jobs(
            conn,
            worker_id="research-test",
            limit=100,
            lease_seconds=60,
        )

    assert created is True
    assert [first["version"], second["version"], third["version"]] == [1, 2, 3]
    assert second["accepted_at"] > first["accepted_at"]
    assert third["accepted_at"] >= second["accepted_at"]
    assert {job["raw_event_id"] for job in jobs} >= {
        first["event_id"],
        second["event_id"],
        third["event_id"],
    }

    leased = next(job for job in jobs if job["raw_event_id"] == third["event_id"])
    async with get_conn() as conn:
        completed = await complete_extraction_job(
            conn,
            job_id=leased["job_id"],
            lease_token=leased["lease_token"],
            succeeded=True,
            error=None,
        )
        stale = await complete_extraction_job(
            conn,
            job_id=leased["job_id"],
            lease_token=leased["lease_token"],
            succeeded=True,
            error=None,
        )
    assert completed is not None and completed["status"] == "completed"
    assert stale is None


@pytest.mark.integration
@pytest.mark.usefixtures("db_pool")
async def test_snapshot_selects_latest_revision_before_scope_filters() -> None:
    now = datetime.now(UTC) - timedelta(minutes=5)
    source = f"snapshot-revision-{uuid4().hex}"
    base = dict(
        source=source,
        source_event_id="logical-event",
        title="provider correction",
        first_seen_at=now,
        fetched_at=now,
        accepted_at=now,
        collector_version="test@1",
        policy_version="structured-provider-latency-v1",
        source_tier="structured",
    )
    async with get_conn() as conn:
        raw_v1, _ = await ingest_raw_event(
            conn,
            RawEventIngestRequest(**base, content="listing BTC"),
        )
        await write_fact(
            conn,
            EventFactWriteRequest(
                raw_event_id=raw_v1["event_id"],
                fact_key="provider:logical-event",
                event_type="listing",
                assets=["BTC"],
                action="lists BTC",
                severity=0.8,
                confidence=0.9,
                effective_at=now,
                available_at=now,
                extractor_version="test@1",
                policy_version="structured-provider-latency-v1",
            ),
        )
        raw_v2, _ = await ingest_raw_event(
            conn,
            RawEventIngestRequest(**base, content="correction: exploit ETH"),
        )
        await write_fact(
            conn,
            EventFactWriteRequest(
                raw_event_id=raw_v2["event_id"],
                fact_key="provider:logical-event",
                event_type="exploit",
                assets=["ETH"],
                action="corrected to ETH exploit",
                severity=1.0,
                confidence=0.95,
                effective_at=now,
                available_at=raw_v2["accepted_at"],
                extractor_version="test@1",
                policy_version="structured-provider-latency-v1",
            ),
        )
        _, facts = await create_snapshot(
            conn,
            EventSnapshotRequest(
                cutoff=raw_v2["accepted_at"] + timedelta(seconds=1),
                policy_version=ALL_VISIBLE_FACTS_POLICY,
                event_types=["listing"],
                assets=["BTC"],
            ),
        )

    assert all(fact["action"] != "lists BTC" for fact in facts)
