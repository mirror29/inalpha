"""A replaced loop worker cannot mutate its baseline or candidate outcomes."""

from decimal import Decimal
from uuid import uuid4

import pytest

from inalpha_evolver.loop_fencing import BaselineLease, BaselineLeaseLost, baseline_lease
from inalpha_evolver.storage import candidates, loop_authorizations, loop_dispatch, loops, runs

from .test_loop_storage import create_baseline, loop_database  # noqa: F401


@pytest.mark.asyncio
async def test_baseline_writes_require_current_loop_worker_lease(database):
    args = await create_baseline(database, uuid4())
    loop = await loops.ensure_for_e1_run(database, **args)
    await loop_authorizations.register(
        database, loop_id=loop["loop_id"], owner_account_id=args["owner_account_id"],
        request_digest="a" * 64, max_cost_usd=Decimal("1"),
    )
    run_id = args["e1_run_id"]
    worker = await loop_dispatch.claim_next(database, worker_id="original")
    lease = BaselineLease(loop["loop_id"], args["owner_account_id"], run_id, worker["lease_token"])
    with pytest.raises(BaselineLeaseLost):
        await runs.transition(database, run_id, from_statuses=("queued",), to_status="running")
    with baseline_lease(lease):
        running = await runs.transition(database, run_id, from_statuses=("queued",), to_status="running")
        assert running["status"] == "running"
        await candidates.insert_slot(database, run_id, 0, "test")
        assert await loop_dispatch.defer(
            database, loop_id=lease.loop_id, owner_account_id=lease.owner_account_id,
            lease_token=lease.lease_token, delay_s=0,
        )
        successor = await loop_dispatch.claim_next(database, worker_id="successor")
        assert successor["lease_token"] != lease.lease_token
        with pytest.raises(BaselineLeaseLost):
            await candidates.update_slot(database, run_id, 0, outcome="succeeded")
        with pytest.raises(BaselineLeaseLost):
            await runs.transition(database, run_id, from_statuses=("running",), to_status="completed")
    assert (await runs.get_run(database, run_id))["status"] == "running"
    assert (await candidates.list_candidates(database, run_id, lease.owner_account_id))[0]["outcome"] == "pending"
