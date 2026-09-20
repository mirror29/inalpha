"""Real budget ledger tests: retries cannot enlarge authorization or refund lost calls."""

from decimal import Decimal
from uuid import uuid4

import pytest
from inalpha_shared.errors import ConflictError

from inalpha_evolver.storage import loop_authorizations as auth
from inalpha_evolver.storage import loop_dispatch, loops

from .test_loop_storage import create_baseline, loop_database  # noqa: F401


@pytest.mark.asyncio
async def test_budget_reservations_bound_retries_and_settle_exactly_once(database):
    args = await create_baseline(database, uuid4())
    loop = await loops.ensure_for_e1_run(database, **args)
    scope = {"loop_id": loop["loop_id"], "owner_account_id": args["owner_account_id"]}
    await auth.register(database, **scope, request_digest="a" * 64, max_cost_usd=Decimal("1"))
    worker = await loop_dispatch.claim_next(database, worker_id="worker", ttl_s=60)
    live = {**scope, "lease_token": worker["lease_token"], "phase": "baseline"}
    reservation = uuid4()
    await auth.reserve(
        database, **live, reservation_id=reservation, step_key="e1:0", amount_usd=Decimal("0.75")
    )
    await auth.reserve(
        database, **live, reservation_id=reservation, step_key="e1:0", amount_usd=Decimal("0.75")
    )
    with pytest.raises(auth.LoopBudgetExhausted):
        await auth.reserve(
            database, **live, reservation_id=uuid4(), step_key="retry", amount_usd=Decimal("0.75")
        )
    await auth.settle(database, **live, reservation_id=reservation, actual_usd=Decimal("0.25"))
    await auth.settle(database, **live, reservation_id=reservation, actual_usd=Decimal("0.25"))
    await auth.reserve(
        database, **live, reservation_id=uuid4(), step_key="e1:1", amount_usd=Decimal("0.75")
    )
    with pytest.raises(auth.LoopBudgetExhausted):
        await auth.reserve(
            database, **live, reservation_id=uuid4(), step_key="excess", amount_usd=Decimal("0.01")
        )
    with pytest.raises(ConflictError):
        await auth.register(database, **scope, request_digest="a" * 64, max_cost_usd=Decimal("2"))


@pytest.mark.asyncio
async def test_expired_worker_cannot_refund_or_reserve_cost(database):
    args = await create_baseline(database, uuid4())
    loop = await loops.ensure_for_e1_run(database, **args)
    scope = {"loop_id": loop["loop_id"], "owner_account_id": args["owner_account_id"]}
    await auth.register(database, **scope, request_digest="a" * 64, max_cost_usd=Decimal("1"))
    worker = await loop_dispatch.claim_next(database, worker_id="worker", ttl_s=60)
    live = {**scope, "lease_token": worker["lease_token"], "phase": "baseline"}
    reservation = uuid4()
    await auth.reserve(
        database, **live, reservation_id=reservation, step_key="e1:0", amount_usd=Decimal("1")
    )
    await loop_dispatch.defer(database, **live_without_phase(live), delay_s=0)
    with pytest.raises(auth.LoopAuthorizationUnavailable):
        await auth.settle(database, **live, reservation_id=reservation, actual_usd=Decimal("0"))
    with pytest.raises(auth.LoopAuthorizationUnavailable):
        await auth.reserve(
            database, **live, reservation_id=uuid4(), step_key="stale", amount_usd=Decimal("0.1")
        )


def live_without_phase(scope):
    return {key: value for key, value in scope.items() if key != "phase"}
