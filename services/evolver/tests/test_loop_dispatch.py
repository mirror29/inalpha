"""Durable loop worker ownership and handoff behavior against PostgreSQL."""

import asyncio
import json
import sys
from uuid import UUID, uuid4

import pytest
from psycopg import AsyncConnection
from psycopg.rows import dict_row

from inalpha_evolver.storage import loops

from .test_loop_storage import (  # noqa: F401
    create_baseline,
    create_campaign,
    database_url,
    loop_database,
)


@pytest.mark.asyncio
async def test_loop_is_claimed_once_until_worker_releases_it(database):
    from inalpha_evolver.storage import loop_dispatch

    args = await create_baseline(database, uuid4())
    loop = await loops.ensure_for_e1_run(database, **args)
    first = await loop_dispatch.claim_next(database, worker_id="worker-a", ttl_s=60)
    assert first["loop_id"] == loop["loop_id"]
    assert await loop_dispatch.claim_next(database, worker_id="worker-b", ttl_s=60) is None
    assert await loop_dispatch.defer(
        database,
        loop_id=loop["loop_id"],
        owner_account_id=args["owner_account_id"],
        lease_token=first["lease_token"],
        delay_s=0,
    )
    second = await loop_dispatch.claim_next(database, worker_id="worker-b", ttl_s=60)
    assert second["loop_id"] == first["loop_id"]
    assert second["lease_token"] != first["lease_token"]


@pytest.mark.asyncio
async def test_only_current_worker_can_finish_baseline_and_retries_are_idempotent(database):
    from inalpha_evolver.storage import loop_dispatch

    args = await create_baseline(database, uuid4())
    loop = await loops.ensure_for_e1_run(database, **args)
    old = await loop_dispatch.claim_next(database, worker_id="old", ttl_s=60)
    await database.execute(
        "UPDATE evolution_loops SET lease_expires_at=clock_timestamp()-INTERVAL '1 second' WHERE loop_id=%s",
        (loop["loop_id"],),
    )
    current = await loop_dispatch.claim_next(database, worker_id="new", ttl_s=60)
    await database.execute(
        "UPDATE strategy_evo_runs SET status='completed' WHERE run_id=%s", (args["e1_run_id"],)
    )
    completion = {
        "loop_id": loop["loop_id"],
        "owner_account_id": args["owner_account_id"],
        "step_key": "baseline",
        "output_id": args["e1_run_id"],
    }
    assert not await loop_dispatch.complete_step(
        database, **completion, lease_token=old["lease_token"]
    )
    assert await loop_dispatch.complete_step(
        database, **completion, lease_token=current["lease_token"]
    )
    first = await loops.get_loop(database, loop["loop_id"], args["owner_account_id"])
    assert first["status"] == "baseline_ready"
    assert await loop_dispatch.complete_step(
        database, **completion, lease_token=current["lease_token"]
    )
    repeated = await loops.get_loop(database, loop["loop_id"], args["owner_account_id"])
    assert repeated["state_version"] == first["state_version"]


@pytest.mark.asyncio
async def test_campaign_handoff_requires_completed_baseline_and_correct_owner(database):
    from inalpha_shared.errors import ConflictError

    from inalpha_evolver.storage import loop_dispatch

    args = await create_baseline(database, uuid4())
    loop = await loops.ensure_for_e1_run(database, **args)
    worker = await loop_dispatch.claim_next(database, worker_id="worker", ttl_s=60)
    scope = {
        "loop_id": loop["loop_id"],
        "owner_account_id": args["owner_account_id"],
        "lease_token": worker["lease_token"],
    }
    campaign_id = await create_campaign(database, args)
    with pytest.raises(ConflictError):
        await loop_dispatch.complete_step(
            database, **scope, step_key="campaign", output_id=campaign_id
        )
    await database.execute(
        "UPDATE strategy_evo_runs SET status='completed' WHERE run_id=%s", (args["e1_run_id"],)
    )
    assert await loop_dispatch.complete_step(
        database, **scope, step_key="baseline", output_id=args["e1_run_id"]
    )
    foreign = await create_campaign(database, args, owner=uuid4())
    with pytest.raises(ConflictError):
        await loop_dispatch.complete_step(database, **scope, step_key="campaign", output_id=foreign)
    assert await loop_dispatch.complete_step(
        database, **scope, step_key="campaign", output_id=campaign_id
    )
    restored = await loops.get_loop(database, loop["loop_id"], args["owner_account_id"])
    assert restored["campaign_id"] == campaign_id
    assert await loop_dispatch.claim_next(database, worker_id="duplicate", ttl_s=60) is None
    replacement = await create_campaign(database, args)
    with pytest.raises(ConflictError):
        await loop_dispatch.complete_step(
            database, **scope, step_key="campaign", output_id=replacement
        )


@pytest.mark.asyncio
async def test_renewal_and_backoff_require_current_owner_and_lease(database):
    from inalpha_evolver.storage import loop_dispatch

    args = await create_baseline(database, uuid4())
    loop = await loops.ensure_for_e1_run(database, **args)
    worker = await loop_dispatch.claim_next(database, worker_id="worker", ttl_s=60)
    scope = {
        "loop_id": loop["loop_id"],
        "owner_account_id": args["owner_account_id"],
        "lease_token": worker["lease_token"],
    }
    assert not await loop_dispatch.renew(
        database, **{**scope, "owner_account_id": uuid4()}, ttl_s=60
    )
    assert not await loop_dispatch.renew(database, **{**scope, "lease_token": uuid4()}, ttl_s=60)
    assert await loop_dispatch.renew(database, **scope, ttl_s=60)
    assert await loop_dispatch.defer(database, **scope, delay_s=30)
    assert await loop_dispatch.claim_next(database, worker_id="too-early", ttl_s=60) is None
    assert not await loop_dispatch.renew(database, **scope, ttl_s=60)


@pytest.mark.asyncio
async def test_competing_connections_only_claim_one_worker_and_restart_respects_lease():
    from inalpha_evolver.storage import loop_dispatch

    owner = uuid4()
    async with await AsyncConnection.connect(
        database_url(), row_factory=dict_row, autocommit=True
    ) as setup:
        args = await create_baseline(setup, owner)
        try:
            await loops.ensure_for_e1_run(setup, **args)

            async def claim(worker):
                async with await AsyncConnection.connect(
                    database_url(), row_factory=dict_row
                ) as conn:
                    return await loop_dispatch.claim_next(conn, worker_id=worker, ttl_s=60)

            results = await asyncio.gather(*(claim(f"worker-{index}") for index in range(4)))
            winners = [row for row in results if row is not None]
            assert len(winners) == 1
            assert await claim("restarted") is None
            await setup.execute(
                "UPDATE evolution_loops SET lease_expires_at=clock_timestamp()-INTERVAL '1 second' WHERE loop_id=%s",
                (winners[0]["loop_id"],),
            )
            restarted = await claim("restarted")
            assert restarted["loop_id"] == winners[0]["loop_id"]
            assert restarted["lease_token"] != winners[0]["lease_token"]
        finally:
            await setup.execute("DELETE FROM evolution_loops WHERE owner_account_id=%s", (owner,))
            await setup.execute("DELETE FROM strategy_evo_runs WHERE owner_account_id=%s", (owner,))


@pytest.mark.asyncio
async def test_abrupt_worker_exit_preserves_lease_and_fences_stale_writes():
    from inalpha_evolver.storage import loop_dispatch

    owner = uuid4()
    async with await AsyncConnection.connect(
        database_url(), row_factory=dict_row, autocommit=True
    ) as setup:
        args = await create_baseline(setup, owner)
        process = None
        try:
            loop = await loops.ensure_for_e1_run(setup, **args)
            process = await asyncio.create_subprocess_exec(
                sys.executable, "-c", """
import asyncio, json, os, sys
from psycopg import AsyncConnection
from psycopg.rows import dict_row
from inalpha_evolver.storage import loop_dispatch

async def claim():
    conn = await AsyncConnection.connect(sys.argv[1], row_factory=dict_row, autocommit=True)
    row = await loop_dispatch.claim_next(conn, worker_id='crashing-process', ttl_s=60)
    print(json.dumps({key: str(row[key]) for key in ('loop_id', 'lease_token')}), flush=True)
    os._exit(23)

asyncio.run(claim())
""", database_url(), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=15)
            assert process.returncode == 23, stderr.decode()
            previous = json.loads(stdout)
            assert UUID(previous["loop_id"]) == loop["loop_id"]
            assert await loop_dispatch.claim_next(setup, worker_id="early-restart") is None
            await setup.execute(
                "UPDATE evolution_loops SET lease_expires_at=clock_timestamp()-INTERVAL '1 second' WHERE loop_id=%s",
                (loop["loop_id"],),
            )
            current = await loop_dispatch.claim_next(setup, worker_id="replacement")
            assert current["loop_id"] == loop["loop_id"]
            stale = {
                "loop_id": loop["loop_id"], "owner_account_id": owner,
                "lease_token": UUID(previous["lease_token"]),
            }
            assert current["lease_token"] != stale["lease_token"]
            assert not await loop_dispatch.renew(setup, **stale)
            assert not await loop_dispatch.record_failure(
                setup, **stale, code="STALE", message="late failure", retryable=False,
            )
            await setup.execute(
                "UPDATE strategy_evo_runs SET status='completed' WHERE run_id=%s",
                (args["e1_run_id"],),
            )
            assert not await loop_dispatch.complete_step(
                setup, **stale, step_key="baseline", output_id=args["e1_run_id"],
            )
            assert await loop_dispatch.complete_step(
                setup, **{**stale, "lease_token": current["lease_token"]},
                step_key="baseline", output_id=args["e1_run_id"],
            )
            restored = await loops.get_loop(setup, loop["loop_id"], owner)
            assert restored["status"] == "baseline_ready"
            assert restored["failure_code"] is None
        finally:
            if process is not None and process.returncode is None:
                process.kill()
                await process.wait()
            await setup.execute("DELETE FROM evolution_loops WHERE owner_account_id=%s", (owner,))
            await setup.execute("DELETE FROM strategy_evo_runs WHERE owner_account_id=%s", (owner,))


@pytest.mark.asyncio
async def test_late_baseline_cannot_resurrect_a_terminal_loop(database):
    from inalpha_evolver.storage import loop_dispatch

    args = await create_baseline(database, uuid4())
    loop = await loops.ensure_for_e1_run(database, **args)
    worker = await loop_dispatch.claim_next(database, worker_id="worker", ttl_s=60)
    await database.execute(
        "UPDATE evolution_loops SET status='failed',failure_code='BUDGET_EXHAUSTED' WHERE loop_id=%s",
        (loop["loop_id"],),
    )
    await database.execute(
        "UPDATE strategy_evo_runs SET status='completed' WHERE run_id=%s", (args["e1_run_id"],)
    )
    assert not await loop_dispatch.complete_step(
        database,
        loop_id=loop["loop_id"],
        owner_account_id=args["owner_account_id"],
        lease_token=worker["lease_token"],
        step_key="baseline",
        output_id=args["e1_run_id"],
    )
    result = await loops.get_loop(database, loop["loop_id"], args["owner_account_id"])
    assert result["status"] == "failed"
    assert result["failure_code"] == "BUDGET_EXHAUSTED"


@pytest.mark.asyncio
async def test_automatic_dispatch_requires_authority_and_retains_retry_reason(database):
    from decimal import Decimal

    from inalpha_evolver.storage import loop_authorizations, loop_dispatch

    args = await create_baseline(database, uuid4())
    loop = await loops.ensure_for_e1_run(database, **args)
    assert await loop_dispatch.claim_next(database, worker_id="automatic", authorized_only=True) is None
    await loop_authorizations.register(
        database, loop_id=loop["loop_id"], owner_account_id=args["owner_account_id"],
        request_digest="a" * 64, max_cost_usd=Decimal("1"),
    )
    first = await loop_dispatch.claim_next(database, worker_id="first", authorized_only=True)
    scope = {key: first[key] for key in ("loop_id", "owner_account_id", "lease_token")}
    assert await loop_dispatch.record_failure(
        database, **scope, code="DEPENDENCY_DOWN", message="retry later", retryable=True,
    )
    retrying = await loops.get_loop(database, loop["loop_id"], args["owner_account_id"])
    assert retrying["failure_code"] == "DEPENDENCY_DOWN"
    assert retrying["status"] == "target_resolved"
    assert await loop_dispatch.claim_next(database, worker_id="too-soon", authorized_only=True) is None
    await database.execute(
        "UPDATE evolution_loops SET next_attempt_at=clock_timestamp() WHERE loop_id=%s",
        (loop["loop_id"],),
    )
    second = await loop_dispatch.claim_next(database, worker_id="second", authorized_only=True)
    assert not await loop_dispatch.record_failure(
        database, **scope, code="STALE", message="late", retryable=False,
    )
    assert await loop_dispatch.record_failure(
        database, **{**scope, "lease_token": second["lease_token"]},
        code="LOOP_BUDGET_EXHAUSTED", message="budget exhausted", retryable=False,
    )
    terminal = await loops.get_loop(database, loop["loop_id"], args["owner_account_id"])
    assert terminal["status"] == "failed"
    assert terminal["failure_code"] == "LOOP_BUDGET_EXHAUSTED"
