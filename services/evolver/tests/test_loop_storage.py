"""Real PostgreSQL coverage for loops that exist before any E2 campaign."""

import asyncio
import hashlib
import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest
import pytest_asyncio
from inalpha_shared.errors import ConflictError, NotFoundError
from psycopg import AsyncConnection
from psycopg.rows import dict_row

from inalpha_evolver.governor.seed import SEED_STRATEGY_CODE
from inalpha_evolver.storage import loop_dispatch, loops, runs

from .llm_snapshot_fixtures import llm_snapshot


def database_url():
    url = os.environ.get("EVOLVER_TEST_DATABASE_URL")
    if not url:
        pytest.skip("EVOLVER_TEST_DATABASE_URL is required for database integration tests")
    return url.replace("postgresql+psycopg://", "postgresql://")


@pytest_asyncio.fixture(name="database")
async def loop_database():
    async with await AsyncConnection.connect(database_url(), row_factory=dict_row) as conn:
        async with conn.transaction(force_rollback=True):
            yield conn


async def create_baseline(conn, owner):
    config = {
        "venue": "binance",
        "symbol": "BTCUSDT",
        "timeframe": "1h",
        "from_ts": "2026-01-01T00:00:00Z",
        "as_of": "2026-02-01T00:00:00Z",
    }
    run, _ = await runs.insert_run(
        conn,
        owner_account_id=owner,
        requested_by_sub=str(owner),
        idempotency_key=str(uuid4()),
        request_hash="a" * 64,
        seed_strategy_id="sma_cross_v1",
        seed_source=SEED_STRATEGY_CODE,
        seed_hash=hashlib.sha256(SEED_STRATEGY_CODE.encode()).hexdigest(),
        budget=4,
        config=config,
        llm_snapshot=llm_snapshot(),
        llm_credential_grant="test-grant-" + "x" * 120,
        queued_at=datetime.now(UTC),
    )
    return {
        "owner_account_id": owner,
        "requested_by_sub": str(owner),
        "operation_id": str(uuid4()),
        "target_kind": "strategy_candidate",
        "target_id": str(uuid4()),
        "target_snapshot": {},
        "e1_run_id": run["run_id"],
        "frozen_config": config,
        "budget": {"e1_candidates": 4},
    }


@pytest.mark.asyncio
async def test_loop_visible_and_idempotent_before_campaign(database):
    args = await create_baseline(database, uuid4())
    first = await loops.ensure_for_e1_run(database, **args)
    repeat = await loops.ensure_for_e1_run(database, **args)
    duplicate_click = await loops.ensure_for_e1_run(
        database, **{**args, "operation_id": str(uuid4())}
    )
    assert first["loop_id"] == repeat["loop_id"] == duplicate_click["loop_id"]
    assert first["campaign_id"] is None
    assert first["status"] == "target_resolved"
    listed = await loops.list_loops(database, args["owner_account_id"], limit=20)
    assert [row["loop_id"] for row in listed] == [first["loop_id"]]
    assert (
        len(
            await loops.list_events(
                database, first["loop_id"], args["owner_account_id"], after_version=-1
            )
        )
        == 1
    )


@pytest.mark.asyncio
async def test_owner_is_checked_before_projecting_baseline(database):
    args = await create_baseline(database, uuid4())
    loop = await loops.ensure_for_e1_run(database, **args)
    await database.execute(
        "UPDATE strategy_evo_runs SET status='completed' WHERE run_id=%s", (args["e1_run_id"],)
    )
    assert await loops.get_loop(database, loop["loop_id"], uuid4()) is None
    cursor = await database.execute(
        "SELECT state_version FROM evolution_loops WHERE loop_id=%s", (loop["loop_id"],)
    )
    assert (await cursor.fetchone())["state_version"] == 0
    updated = await loops.get_loop(database, loop["loop_id"], args["owner_account_id"])
    assert updated["status"] == "baseline_ready"
    assert updated["state_version"] == 1
    repeated = await loops.get_loop(database, loop["loop_id"], args["owner_account_id"])
    assert repeated["state_version"] == 1


@pytest.mark.asyncio
async def test_loop_rejects_foreign_baseline_and_reused_operation(database):
    args = await create_baseline(database, uuid4())
    with pytest.raises(NotFoundError):
        await loops.ensure_for_e1_run(database, **{**args, "owner_account_id": uuid4()})
    await loops.ensure_for_e1_run(database, **args)
    with pytest.raises(ConflictError):
        await loops.ensure_for_e1_run(database, **{**args, "target_id": str(uuid4())})
    with pytest.raises(ConflictError):
        await loops.ensure_for_e1_run(database, **{**args, "budget": {"e1_candidates": 8}})


@pytest.mark.asyncio
async def test_failed_baseline_projects_actionable_terminal_state(database):
    args = await create_baseline(database, uuid4())
    loop = await loops.ensure_for_e1_run(database, **args)
    await database.execute(
        """UPDATE strategy_evo_runs SET status='failed',failure_code='DATA_UNAVAILABLE',
failure_message='upstream failed',finished_at=NOW() WHERE run_id=%s""",
        (args["e1_run_id"],),
    )
    updated = await loops.get_loop(database, loop["loop_id"], args["owner_account_id"])
    assert updated["status"] == "failed"
    assert updated["failure_code"] == "DATA_UNAVAILABLE"
    assert updated["finished_at"] is not None
    assert (
        await loops.get_active_loop_for_target(
            database, args["owner_account_id"], args["target_kind"], args["target_id"]
        )
        is None
    )


async def create_campaign(database, args, *, owner=None):
    campaign_id, snapshot_id = uuid4(), uuid4()
    await database.execute(
        """INSERT INTO market_event_snapshots(snapshot_id,cutoff,policy_version,query_hash,events_sha256,fact_count)
VALUES(%s,NOW(),'test-v1',%s,%s,0)""",
        (snapshot_id, hashlib.sha256(str(snapshot_id).encode()).hexdigest(), "a" * 64),
    )
    await database.execute(
        """INSERT INTO evolution_campaigns(campaign_id,owner_account_id,requested_by_sub,
idempotency_key,request_hash,source_run_id,event_snapshot_id,frozen_config,llm_snapshot,llm_config_digest)
VALUES(%s,%s,'test',%s,%s,%s,%s,'{}','{}',%s)""",
        (
            campaign_id,
            owner or args["owner_account_id"],
            str(uuid4()),
            "a" * 64,
            args["e1_run_id"],
            snapshot_id,
            "b" * 64,
        ),
    )
    return campaign_id


@pytest.mark.asyncio
async def test_campaign_attaches_to_the_original_loop_once(database):
    args = await create_baseline(database, uuid4())
    original = await loops.ensure_for_e1_run(database, **args)
    campaign_id = await create_campaign(database, args)
    with pytest.raises(ConflictError):
        await loops.ensure_for_campaign(database, **args, campaign_id=campaign_id)
    worker = await loop_dispatch.claim_next(database, worker_id="worker", ttl_s=60)
    await database.execute(
        "UPDATE strategy_evo_runs SET status='completed' WHERE run_id=%s", (args["e1_run_id"],)
    )
    assert await loop_dispatch.complete_step(
        database,
        loop_id=original["loop_id"],
        owner_account_id=args["owner_account_id"],
        lease_token=worker["lease_token"],
        step_key="baseline",
        output_id=args["e1_run_id"],
    )
    linked = await loops.ensure_for_campaign(
        database, **args, campaign_id=campaign_id, lease_token=worker["lease_token"]
    )
    repeated = await loops.ensure_for_campaign(database, **args, campaign_id=campaign_id)
    assert linked["loop_id"] == original["loop_id"] == repeated["loop_id"]
    assert linked["campaign_id"] == campaign_id
    assert repeated["state_version"] == linked["state_version"] == 2
    replacement = await create_campaign(database, args)
    with pytest.raises(ConflictError):
        await loops.ensure_for_campaign(database, **args, campaign_id=replacement)
    events = await loops.list_events(
        database, original["loop_id"], args["owner_account_id"], after_version=-1
    )
    assert [row["event_type"] for row in events] == [
        "loop_created",
        "step_completed",
        "step_completed",
    ]


@pytest.mark.asyncio
async def test_foreign_campaign_cannot_attach_to_baseline_loop(database):
    args = await create_baseline(database, uuid4())
    original = await loops.ensure_for_e1_run(database, **args)
    campaign_id = await create_campaign(database, args, owner=uuid4())
    with pytest.raises(ConflictError):
        await loops.ensure_for_campaign(database, **args, campaign_id=campaign_id)
    unchanged = await loops.get_loop(database, original["loop_id"], args["owner_account_id"])
    assert unchanged["campaign_id"] is None


@pytest.mark.asyncio
async def test_concurrent_clicks_reuse_one_persisted_loop_after_reconnect():
    owner = uuid4()
    async with await AsyncConnection.connect(
        database_url(), row_factory=dict_row, autocommit=True
    ) as setup:
        args = await create_baseline(setup, owner)
        try:

            async def click():
                async with await AsyncConnection.connect(
                    database_url(), row_factory=dict_row
                ) as conn:
                    return await loops.ensure_for_e1_run(
                        conn,
                        **{**args, "operation_id": str(uuid4())},
                    )

            results = await asyncio.gather(*(click() for _ in range(4)))
            assert len({result["loop_id"] for result in results}) == 1
            async with await AsyncConnection.connect(
                database_url(), row_factory=dict_row
            ) as reconnected:
                restored = await loops.get_loop(reconnected, results[0]["loop_id"], owner)
                assert restored["e1_run_id"] == args["e1_run_id"]
                assert restored["state_version"] == 0
        finally:
            await setup.execute("DELETE FROM evolution_loops WHERE owner_account_id=%s", (owner,))
            await setup.execute("DELETE FROM strategy_evo_runs WHERE owner_account_id=%s", (owner,))
