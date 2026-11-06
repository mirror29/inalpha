"""Evolver DB storage 集成测试。"""

from __future__ import annotations

import hashlib
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from inalpha_shared.db import close_pool, get_conn, init_pool

from inalpha_evolver.governor.seed import SEED_STRATEGY_CODE
from inalpha_evolver.storage import candidates, run_queries, runs

from .llm_snapshot_fixtures import llm_snapshot


@pytest.mark.asyncio
async def test_run_idempotency_owner_scope_and_slot() -> None:
    url = os.environ.get("EVOLVER_TEST_DATABASE_URL")
    if not url:
        pytest.skip("EVOLVER_TEST_DATABASE_URL is required for database integration tests")

    await init_pool(url)
    try:
        owner = uuid4()
        other = uuid4()
        now = datetime.now(UTC)
        config = {
            "venue": "binance",
            "symbol": "BTCUSDT",
            "timeframe": "1h",
            "from_ts": now - timedelta(days=1),
            "as_of": now,
            "initial_cash": 10_000.0,
        }
        kwargs = {
            "owner_account_id": owner,
            "requested_by_sub": "storage-test",
            "idempotency_key": f"test-{uuid4()}",
            "request_hash": "hash-a",
            "seed_strategy_id": "sma_cross_v1",
            "seed_source": SEED_STRATEGY_CODE,
            "seed_hash": hashlib.sha256(SEED_STRATEGY_CODE.encode()).hexdigest(),
            "budget": 2,
            "config": config,
            "llm_snapshot": llm_snapshot(),
            "llm_credential_grant": "signed-grant-" + "x" * 120,
            "queued_at": now,
        }

        async with get_conn() as conn:
            row, created = await runs.insert_run(conn, **kwargs)
            repeated, created_again = await runs.insert_run(conn, **kwargs)

            stale_kwargs = {
                **kwargs,
                "idempotency_key": f"stale-{uuid4()}",
                "request_hash": "hash-stale",
                "queued_at": now - timedelta(days=2),
            }
            stale, _ = await runs.insert_run(conn, **stale_kwargs)

            assert created is True
            assert created_again is False
            assert repeated["run_id"] == row["run_id"]
            assert await runs.get_run(conn, row["run_id"], other) is None

            claimed = await run_queries.claim_next(conn)
            assert claimed and claimed["status"] == "running"
            assert claimed["run_id"] == row["run_id"]

            stale_after = await runs.get_run(conn, stale["run_id"], owner)
            assert stale_after and stale_after["status"] == "aborted"
            assert stale_after["failure_code"] == "EVOLUTION_QUEUE_TIMEOUT"

            await runs.clear_credential_grant(conn, claimed["run_id"])
            claimed_after = await runs.get_run(conn, claimed["run_id"], owner)
            assert claimed_after and claimed_after["llm_credential_grant"] is None

            slot = await candidates.insert_slot(conn, row["run_id"], 0, "hint")
            assert slot["slot"] == 0
            assert await candidates.list_candidates(conn, row["run_id"], other) == []

    finally:
        await close_pool()


@pytest.mark.asyncio
async def test_candidate_source_duplicate_detection_is_exact_and_run_scoped() -> None:
    url = os.environ.get("EVOLVER_TEST_DATABASE_URL")
    if not url:
        pytest.skip("EVOLVER_TEST_DATABASE_URL is required for database integration tests")

    await init_pool(url)
    try:
        owner = uuid4()
        now = datetime.now(UTC)
        config = {
            "venue": "binance",
            "symbol": "BTCUSDT",
            "timeframe": "1h",
            "from_ts": now - timedelta(days=1),
            "as_of": now,
            "initial_cash": 10_000.0,
        }

        kwargs = {
            "owner_account_id": owner,
            "requested_by_sub": "storage-test",
            "idempotency_key": f"source-test-{uuid4()}",
            "request_hash": "source-test-hash-a",
            "seed_strategy_id": "sma_cross_v1",
            "seed_source": SEED_STRATEGY_CODE,
            "seed_hash": hashlib.sha256(SEED_STRATEGY_CODE.encode()).hexdigest(),
            "budget": 2,
            "config": config,
            "llm_snapshot": llm_snapshot(),
            "llm_credential_grant": "signed-grant-" + "x" * 120,
            "queued_at": now,
        }

        async with get_conn() as conn:
            first_run, first_created = await runs.insert_run(conn, **kwargs)

            second_kwargs = {
                **kwargs,
                "idempotency_key": f"source-test-{uuid4()}",
                "request_hash": "source-test-hash-b",
            }
            second_run, second_created = await runs.insert_run(
                conn, **second_kwargs
            )

            assert first_created is True
            assert second_created is True
            assert first_run["run_id"] != second_run["run_id"]

            await candidates.insert_slot(
                conn,
                first_run["run_id"],
                0,
                "source duplicate test",
            )

            source = "class TestStrategy:\n    value = 1\n"
            source_hash = hashlib.sha256(source.encode()).hexdigest()
            different_hash = hashlib.sha256(b"different source").hexdigest()

            await candidates.update_slot(
                conn,
                first_run["run_id"],
                0,
                stage="evaluation",
                source_code=source,
                source_hash=source_hash,
            )

            assert await candidates.source_exists(
                conn, first_run["run_id"], source_hash
            ) is True

            assert await candidates.source_exists(
                conn, first_run["run_id"], different_hash
            ) is False

            assert await candidates.source_exists(
                conn, second_run["run_id"], source_hash
            ) is False

            summary = await candidates.summarize(conn, first_run["run_id"])

            assert summary["attempted"] == 1
            assert summary["succeeded"] == 0

    finally:
        await close_pool()