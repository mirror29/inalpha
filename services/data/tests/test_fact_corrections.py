"""Prospective reviews must preserve PIT, audit identity, and atomic idempotency."""

import hashlib
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from inalpha_shared.db import get_conn
from inalpha_shared.errors import ConflictError, InalphaError
from pydantic import ValidationError

from inalpha_data.event_models import (
    EventFactCorrectionRequest,
    EventFactWriteRequest,
    EventSnapshotRequest,
    RawEventIngestRequest,
)
from inalpha_data.storage import fact_corrections
from inalpha_data.storage.events import (
    ALL_VISIBLE_FACTS_POLICY,
    create_snapshot,
    ingest_raw_event,
    write_fact,
)


def review(source, *, retracted=False):
    return EventFactCorrectionRequest.model_validate(
        {
            "correction_id": str(uuid4()),
            "expected_fact_hash": source["fact_hash"],
            "reason": "Deterministic v3 review of the legacy ban substring classification",
            "value": {
                "event_type": "other",
                "assets": source["assets"],
                "actor": source["actor"],
                "action": source["action"],
                "severity": 0.2,
                "confidence": 0.35,
                "evidence_spans": source["evidence_spans"],
                "extractor_version": "deterministic-event-extractor-v3",
                "retracted": retracted,
            },
        }
    )


async def seed(conn, *, assets=None):
    now = datetime.now(UTC) - timedelta(minutes=5)
    raw, _ = await ingest_raw_event(
        conn,
        RawEventIngestRequest(
            source="correction-test-" + uuid4().hex,
            source_event_id="article",
            title="Bitcoin bank custody",
            first_seen_at=now,
            fetched_at=now,
            accepted_at=now,
            collector_version="test",
            policy_version="first-seen-only-v1",
            source_tier="official",
        ),
    )
    source, _ = await write_fact(
        conn,
        EventFactWriteRequest(
            raw_event_id=raw["event_id"],
            fact_key=hashlib.sha256(
                f"{raw['source']}\0{raw['source_event_id']}\0regulatory".encode()
            ).hexdigest()[:48],
            event_type="regulatory",
            assets=assets if assets is not None else ["BTC"],
            action="Bitcoin bank custody",
            severity=0.6,
            confidence=0.85,
            effective_at=now,
            available_at=now,
            extractor_version="deterministic-event-extractor-v2",
            policy_version="first-seen-only-v1",
        ),
    )
    return source


async def snapshot(conn, cutoff):
    return await create_snapshot(
        conn,
        EventSnapshotRequest(
            cutoff=cutoff,
            policy_version=ALL_VISIBLE_FACTS_POLICY,
            assets=["BTC"],
        ),
    )


def test_review_never_accepts_client_clock_or_fact_identity():
    source = {
        "fact_hash": "a" * 64,
        "assets": ["BTC"],
        "actor": None,
        "action": "bank",
        "evidence_spans": [],
    }
    data = review(source).model_dump(mode="json")
    for field in ("available_at", "raw_event_id", "fact_key", "effective_at"):
        with pytest.raises(ValidationError):
            EventFactCorrectionRequest.model_validate(
                {**data, "value": {**data["value"], field: "forbidden"}}
            )


@pytest.mark.integration
@pytest.mark.usefixtures("db_pool")
@pytest.mark.parametrize("retracted", [False, True])
async def test_review_preserves_old_snapshot_and_recovers_same_result(retracted):
    async with get_conn() as conn:
        source = await seed(conn)
        old_cutoff = datetime.now(UTC)
        old_snapshot, _ = await snapshot(conn, old_cutoff)
        request = review(source, retracted=retracted)
        audit, corrected, created = await fact_corrections.correct_fact(
            conn, source["fact_id"], request, service_subject="service:research"
        )
        assert created and corrected["available_at"] > old_cutoff
        assert corrected["effective_at"] == source["effective_at"]
        assert (corrected["fact_key"] == source["fact_key"]) is retracted
        assert audit["retired_fact_id"] is not None
        assert (corrected["supersedes_fact_id"] == source["fact_id"]) is retracted
        assert corrected["version"] == (2 if retracted else 1)
        repeated_audit, repeated, created = await fact_corrections.correct_fact(
            conn, source["fact_id"], request, service_subject="service:research"
        )
        assert (
            not created and repeated["fact_id"] == corrected["fact_id"] and repeated_audit == audit
        )
        _, old_facts = await snapshot(conn, old_cutoff)
        assert any(f["fact_id"] == source["fact_id"] for f in old_facts)
        _, new_facts = await snapshot(conn, datetime.now(UTC))
        assert not any(f["fact_id"] == source["fact_id"] for f in new_facts)
        assert any(f["fact_id"] == corrected["fact_id"] for f in new_facts) is not retracted
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT fact_id FROM market_event_snapshot_facts WHERE snapshot_id=%s",
                (old_snapshot["snapshot_id"],),
            )
            assert source["fact_id"] in [r["fact_id"] for r in await cur.fetchall()]
        for changed in (request.model_copy(update={"reason": "different review"}), review(source)):
            with pytest.raises(ConflictError):
                await fact_corrections.correct_fact(
                    conn, source["fact_id"], changed, service_subject="service:research"
                )
        with pytest.raises(ConflictError):
            await fact_corrections.correct_fact(
                conn, source["fact_id"], request, service_subject="service:other"
            )


@pytest.mark.integration
@pytest.mark.usefixtures("db_pool")
@pytest.mark.parametrize("fail_after", [1, 2])
async def test_review_failure_rolls_back_both_fact_and_audit(monkeypatch, fail_after):
    original = fact_corrections.write_fact
    calls = 0

    async def fail_after_write(*args, **kwargs):
        nonlocal calls
        result = await original(*args, **kwargs)
        calls += 1
        if calls == fail_after:
            raise RuntimeError("injected failure after fact append")
        return result

    async with get_conn() as conn:
        source = await seed(conn)
        request = review(source)
        monkeypatch.setattr(fact_corrections, "write_fact", fail_after_write)
        with pytest.raises(RuntimeError):
            await fact_corrections.correct_fact(
                conn, source["fact_id"], request, service_subject="service:research"
            )
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT count(*) AS n FROM market_event_facts WHERE raw_event_id=%s",
                (source["raw_event_id"],),
            )
            assert (await cur.fetchone())["n"] == 1
            await cur.execute(
                "SELECT count(*) AS n FROM market_event_fact_corrections WHERE correction_id=%s",
                (request.correction_id,),
            )
            assert (await cur.fetchone())["n"] == 0
        monkeypatch.setattr(fact_corrections, "write_fact", original)
        _, _, created = await fact_corrections.correct_fact(
            conn, source["fact_id"], request, service_subject="service:research"
        )
        assert created


@pytest.mark.integration
@pytest.mark.usefixtures("db_pool")
async def test_unverified_append_aborts_audit_even_under_optimization(monkeypatch):
    original = fact_corrections.write_fact

    async def unverified_append(*args, **kwargs):
        fact, _created = await original(*args, **kwargs)
        return fact, False

    async with get_conn() as conn:
        source = await seed(conn)
        monkeypatch.setattr(fact_corrections, "write_fact", unverified_append)
        with pytest.raises(InalphaError) as caught:
            await fact_corrections.correct_fact(
                conn, source["fact_id"], review(source), service_subject="service:research"
            )
        assert caught.value.code == "EVENT_FACT_CORRECTION_INVARIANT"
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT count(*) AS n FROM market_event_facts WHERE raw_event_id=%s",
                (source["raw_event_id"],),
            )
            assert (await cur.fetchone())["n"] == 1
            await cur.execute(
                "SELECT count(*) AS n FROM market_event_fact_corrections WHERE source_fact_id=%s",
                (source["fact_id"],),
            )
            assert (await cur.fetchone())["n"] == 0


@pytest.mark.integration
@pytest.mark.usefixtures("db_pool")
async def test_review_rejects_changed_evidence_and_wrong_source_digest():
    async with get_conn() as conn:
        source = await seed(conn)
        request = review(source)
        changed = request.model_copy(update={"expected_fact_hash": "0" * 64})
        with pytest.raises(ConflictError):
            await fact_corrections.correct_fact(
                conn, source["fact_id"], changed, service_subject="service:research"
            )
        tampered = request.model_copy(
            update={"value": request.value.model_copy(update={"assets": ["ETH"]})}
        )
        with pytest.raises(ConflictError):
            await fact_corrections.correct_fact(
                conn, source["fact_id"], tampered, service_subject="service:research"
            )


@pytest.mark.integration
@pytest.mark.usefixtures("db_pool")
async def test_regular_extraction_after_correction_uses_one_visible_classification():
    async with get_conn() as conn:
        source = await seed(conn)
        request = review(source)
        audit, corrected, _ = await fact_corrections.correct_fact(
            conn, source["fact_id"], request, service_subject="service:research"
        )
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT * FROM raw_market_events WHERE event_id=%s", (source["raw_event_id"],)
            )
            original_raw = await cur.fetchone()
        now = datetime.now(UTC)
        updated_raw, _ = await ingest_raw_event(
            conn,
            RawEventIngestRequest(
                source=original_raw["source"],
                source_event_id=original_raw["source_event_id"],
                title="Bitcoin bank custody updated",
                first_seen_at=original_raw["first_seen_at"],
                fetched_at=now,
                accepted_at=now,
                collector_version="test",
                policy_version="first-seen-only-v1",
                source_tier="official",
            ),
        )
        extracted, _ = await write_fact(
            conn,
            EventFactWriteRequest(
                **request.value.model_dump(),
                raw_event_id=updated_raw["event_id"],
                fact_key=corrected["fact_key"],
                effective_at=source["effective_at"],
                available_at=updated_raw["accepted_at"],
                policy_version="first-seen-only-v1",
            ),
        )
        _, visible = await snapshot(conn, datetime.now(UTC))
        family_ids = {
            source["fact_id"],
            audit["retired_fact_id"],
            corrected["fact_id"],
            extracted["fact_id"],
        }
        assert [fact["fact_id"] for fact in visible if fact["fact_id"] in family_ids] == [
            extracted["fact_id"]
        ]


@pytest.mark.integration
@pytest.mark.usefixtures("db_pool")
async def test_same_classification_review_appends_one_version_without_retirement():
    async with get_conn() as conn:
        source = await seed(conn)
        request = review(source)
        request = request.model_copy(
            update={"value": request.value.model_copy(update={"event_type": "regulatory"})}
        )
        audit, corrected, _ = await fact_corrections.correct_fact(
            conn, source["fact_id"], request, service_subject="service:research"
        )
        assert audit["retired_fact_id"] is None
        assert corrected["fact_key"] == source["fact_key"]
        assert corrected["supersedes_fact_id"] == source["fact_id"]
        assert corrected["version"] == 2


@pytest.mark.integration
@pytest.mark.usefixtures("db_pool")
async def test_regular_writer_canonicalizes_legacy_multi_asset_review():
    async with get_conn() as conn:
        source = await seed(conn, assets=["eth", "BTC", "btc"])
        assert source["assets"] == ["BTC", "ETH"]
        _, corrected, _ = await fact_corrections.correct_fact(
            conn, source["fact_id"], review(source), service_subject="service:research"
        )
        assert corrected["assets"] == source["assets"]


@pytest.mark.integration
@pytest.mark.usefixtures("db_pool")
async def test_pending_new_raw_evidence_prevents_revalidating_stale_fact():
    async with get_conn() as conn:
        source = await seed(conn)
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT source,source_event_id,first_seen_at FROM raw_market_events WHERE event_id=%s",
                (source["raw_event_id"],),
            )
            raw = await cur.fetchone()
        now = datetime.now(UTC)
        await ingest_raw_event(
            conn,
            RawEventIngestRequest(
                source=raw["source"],
                source_event_id=raw["source_event_id"],
                title="Bitcoin bank lawsuit update",
                first_seen_at=raw["first_seen_at"],
                fetched_at=now,
                accepted_at=now,
                collector_version="test",
                policy_version="first-seen-only-v1",
                source_tier="official",
            ),
        )
        with pytest.raises(ConflictError):
            await fact_corrections.correct_fact(
                conn, source["fact_id"], review(source), service_subject="service:research"
            )
        _, retired, created = await fact_corrections.correct_fact(
            conn,
            source["fact_id"],
            review(source, retracted=True),
            service_subject="service:research",
        )
        assert created and retired["retracted"]


@pytest.mark.integration
@pytest.mark.usefixtures("db_pool")
async def test_existing_replacement_requires_explicit_retirement_without_overwriting():
    async with get_conn() as conn:
        source = await seed(conn)
        request = review(source)
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT source,source_event_id FROM raw_market_events WHERE event_id=%s",
                (source["raw_event_id"],),
            )
            raw = await cur.fetchone()
        replacement, _ = await write_fact(
            conn,
            EventFactWriteRequest(
                **request.value.model_dump(),
                raw_event_id=source["raw_event_id"],
                fact_key=hashlib.sha256(
                    f"{raw['source']}\0{raw['source_event_id']}\0other".encode()
                ).hexdigest()[:48],
                effective_at=source["effective_at"],
                available_at=datetime.now(UTC),
                policy_version="first-seen-only-v1",
            ),
        )
        with pytest.raises(ConflictError):
            await fact_corrections.correct_fact(
                conn, source["fact_id"], request, service_subject="service:research"
            )
        retirement = review(source, retracted=True)
        audit, _, _ = await fact_corrections.correct_fact(
            conn, source["fact_id"], retirement, service_subject="service:research"
        )
        _, visible = await snapshot(conn, datetime.now(UTC))
        assert any(fact["fact_id"] == replacement["fact_id"] for fact in visible)
        assert not any(
            fact["fact_id"] in (source["fact_id"], audit["retired_fact_id"]) for fact in visible
        )


@pytest.mark.integration
@pytest.mark.usefixtures("db_pool")
async def test_regular_writer_serializes_against_review_and_makes_old_review_stale():
    import asyncio

    from inalpha_data.storage.events import _advisory_lock_key

    async with get_conn() as conn:
        source = await seed(conn)
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT source,source_event_id FROM raw_market_events WHERE event_id=%s",
                (source["raw_event_id"],),
            )
            raw = await cur.fetchone()
    async with get_conn() as writer:
        async with writer.cursor() as cur:
            await cur.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                (_advisory_lock_key(raw["source"], raw["source_event_id"]),),
            )

        async def attempt():
            async with get_conn() as reviewer:
                return await fact_corrections.correct_fact(
                    reviewer, source["fact_id"], review(source), service_subject="service:research"
                )

        pending = asyncio.create_task(attempt())
        try:
            # The held family lock blocks the review until the regular version commits.
            await asyncio.sleep(0.05)
            assert not pending.done()
            values = {
                key: source[key]
                for key in (
                    "raw_event_id",
                    "fact_key",
                    "event_type",
                    "assets",
                    "actor",
                    "action",
                    "severity",
                    "confidence",
                    "effective_at",
                    "evidence_spans",
                    "extractor_version",
                    "policy_version",
                )
            }
            await write_fact(
                writer, EventFactWriteRequest(**values, available_at=datetime.now(UTC))
            )
            await writer.commit()
            with pytest.raises(ConflictError):
                await asyncio.wait_for(pending, timeout=5)
        finally:
            if not pending.done():
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)


@pytest.mark.integration
@pytest.mark.usefixtures("db_pool")
@pytest.mark.parametrize("same_operation", [True, False])
async def test_concurrent_reviews_do_not_append_twice(same_operation):
    import asyncio

    async with get_conn() as conn:
        source = await seed(conn)
    first = review(source)
    second = first if same_operation else review(source)

    async def attempt(request):
        async with get_conn() as conn:
            return await fact_corrections.correct_fact(
                conn, source["fact_id"], request, service_subject="service:research"
            )

    results = await asyncio.gather(attempt(first), attempt(second), return_exceptions=True)
    if same_operation:
        assert sorted(result[2] for result in results) == [False, True]
        assert results[0][1]["fact_id"] == results[1][1]["fact_id"]
    else:
        assert sum(isinstance(result, ConflictError) for result in results) == 1
    async with get_conn() as conn, conn.cursor() as cur:
        await cur.execute(
            "SELECT count(*) AS n FROM market_event_facts WHERE raw_event_id=%s",
            (source["raw_event_id"],),
        )
        assert (await cur.fetchone())["n"] == 3
        await cur.execute(
            "SELECT count(*) AS n FROM market_event_fact_corrections WHERE source_fact_id=%s",
            (source["fact_id"],),
        )
        assert (await cur.fetchone())["n"] == 1


@pytest.mark.parametrize(
    "mode",
    [
        "missing",
        "owner",
        "wrong_purpose",
        "service",
        "service_conflict",
        "service_invariant",
        "service_missing",
        "service_internal_key",
    ],
)
def test_correction_route_requires_existing_extraction_authority(monkeypatch, mode):
    import time
    from unittest.mock import AsyncMock

    import jwt
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from inalpha_shared import install_error_handler
    from inalpha_shared.config import Settings, get_settings
    from inalpha_shared.db import _db_dep

    from inalpha_data.api import events

    secret = "correction-tests-only-secret-with-at-least-32-bytes"
    settings = Settings(DATABASE_URL="postgresql://unused", JWT_SECRET=secret)
    app = FastAPI()
    install_error_handler(app)
    app.include_router(events.router, prefix="/api/v1")
    app.dependency_overrides[get_settings] = lambda: settings

    async def unused_database():
        yield object()

    app.dependency_overrides[_db_dep] = unused_database
    mutation = AsyncMock()
    monkeypatch.setattr(events, "correct_fact", mutation)
    source = {
        "fact_hash": "a" * 64,
        "assets": ["BTC"],
        "actor": None,
        "action": "bank",
        "evidence_spans": [],
    }
    headers = {}
    if mode != "missing":
        now = int(time.time())
        payload = {
            "sub": "service:test",
            "token_use": "owner" if mode == "owner" else "service",
            "service_audience": "data",
            "token_purpose": "event_snapshot_read" if mode == "wrong_purpose" else "event_extract",
            "iat": now,
            "exp": now + 300,
        }
        headers = {"Authorization": "Bearer " + jwt.encode(payload, secret, algorithm="HS256")}
    source_id, corrected_id, now = uuid4(), uuid4(), datetime.now(UTC)
    corrected = {
        **review(source).value.model_dump(mode="json"),
        "fact_id": corrected_id,
        "raw_event_id": uuid4(),
        "fact_key": "original-key",
        "version": 2,
        "fact_hash": "b" * 64,
        "asset_ids": ["asset:BTC"],
        "available_at": now,
        "effective_at": now - timedelta(days=1),
        "created_at": now,
        "policy_version": "event-fact-correction-v1",
        "supersedes_fact_id": source_id,
    }
    mutation.return_value = (
        {
            "correction_id": uuid4(),
            "source_fact_id": source_id,
            "corrected_fact_id": corrected_id,
            "request_hash": "c" * 64,
            "service_subject": "service:test",
            "reason": "verified fixture review",
            "available_at": now,
        },
        corrected,
        True,
    )
    if mode == "service_conflict":
        mutation.side_effect = ConflictError(
            "stale fixture source", code="EVENT_FACT_CORRECTION_CONFLICT"
        )
    if mode == "service_invariant":
        mutation.side_effect = ValueError("private fixture invariant detail")
    if mode == "service_missing":
        mutation.side_effect = fact_corrections.CorrectionSourceNotFoundError(
            "source fact not found"
        )
    if mode == "service_internal_key":
        mutation.side_effect = KeyError("private fixture internal column")
    read = AsyncMock(return_value=corrected)
    monkeypatch.setattr(events.store, "get_fact", read)
    # Exercise the real dependency without opening a database or widening owner rights.
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(
            f"/api/v1/events/facts/{source_id}/corrections",
            json=review(source).model_dump(mode="json"),
            headers=headers,
        )
        exact_read = client.get(f"/api/v1/events/facts/{corrected_id}", headers=headers)
    if mode.startswith("service"):
        expected = {
            "service": 200,
            "service_conflict": 409,
            "service_invariant": 500,
            "service_missing": 404,
            "service_internal_key": 500,
        }[mode]
        assert response.status_code == expected
        assert exact_read.status_code == 200
        assert exact_read.json()["fact_id"] == str(corrected_id)
        read.assert_awaited_once()
        if mode == "service":
            assert response.json()["fact"]["fact_id"] == str(corrected_id)
        elif mode == "service_conflict":
            assert response.json()["code"] == "EVENT_FACT_CORRECTION_CONFLICT"
        elif mode == "service_invariant":
            assert "private fixture invariant detail" not in response.text
            assert response.json()["code"] == "EVENT_FACT_CORRECTION_INVARIANT"
        elif mode == "service_internal_key":
            assert "private fixture internal column" not in response.text
            assert response.json()["code"] == "INTERNAL_ERROR"
        mutation.assert_awaited_once()
        assert mutation.await_args.kwargs["service_subject"] == "service:test"
    else:
        assert response.status_code == 401
        assert exact_read.status_code == 401
        read.assert_not_awaited()
        mutation.assert_not_awaited()
