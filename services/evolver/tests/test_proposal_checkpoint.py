"""Committed generations do not purchase a second proposal after a worker restart."""

from uuid import uuid4

import pytest

from inalpha_evolver.hypothesis.seeding import seed_generation_one
from inalpha_evolver.storage import campaigns, proposal_checkpoints

from .test_loop_storage import create_baseline, create_campaign, loop_database  # noqa: F401


@pytest.mark.asyncio
async def test_proposal_and_cost_commit_once_and_reject_stale_worker(database):
    args = await create_baseline(database, uuid4())
    campaign_id = await create_campaign(database, args)
    lease = await campaigns.acquire_lease(database, campaign_id, worker_id="first", ttl_s=60)
    await campaigns.transition(
        database, campaign_id, args["owner_account_id"], from_statuses=("draft",),
        to_status="replaying", values={"active_generation": 1}, lease_token=lease["lease_token"],
    )
    hypotheses = seed_generation_one({"facts": []}, "BTC", "asset:BTC")
    scope = {
        "campaign_id": campaign_id, "generation": 1, "hypotheses": hypotheses,
        "lease_token": lease["lease_token"], "cost_usd": 0.1, "fallback_calls": 0,
    }
    assert await proposal_checkpoints.commit_proposal(database, **scope)
    assert not await proposal_checkpoints.commit_proposal(database, **scope)
    restored = await proposal_checkpoints.get_proposal(database, campaign_id, 1)
    assert restored is not None
    campaign = await campaigns.get_campaign(database, campaign_id, args["owner_account_id"])
    assert len(campaign["hypotheses"]) == 8
    assert campaign["llm_cost_usd"] == pytest.approx(0.1)
    with pytest.raises(RuntimeError, match="lease"):
        await proposal_checkpoints.commit_proposal(database, **{**scope, "lease_token": uuid4()})
