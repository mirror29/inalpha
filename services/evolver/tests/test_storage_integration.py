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
