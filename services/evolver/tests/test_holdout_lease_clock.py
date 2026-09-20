"""Holdout fencing must use elapsed database time, not transaction start time."""

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from inalpha_evolver.config import EvolverSettings
from inalpha_evolver.hypothesis.compiler import compile_hypothesis
from inalpha_evolver.hypothesis.models import HypothesisSpec
from inalpha_evolver.runtime import loop_campaign
from inalpha_evolver.storage import campaigns

from .test_loop_handoff import ready_loop
from .test_loop_storage import create_baseline, create_campaign, loop_database  # noqa: F401


@pytest.mark.asyncio
async def test_campaign_lease_uses_database_clock_when_worker_clock_is_wrong(database, monkeypatch):
    args = await create_baseline(database, uuid4())
    campaign_id = await create_campaign(database, args)

    class SkewedClock:
        @staticmethod
        def now(tz):
            return datetime(2000, 1, 1, tzinfo=UTC)

    monkeypatch.setattr(campaigns, "datetime", SkewedClock)
    lease = await campaigns.acquire_lease(database, campaign_id, worker_id="skewed", ttl_s=60)
    await campaigns.assert_active_lease(database, campaign_id, lease["lease_token"])
    assert await campaigns.renew_lease(
        database, campaign_id, worker_id="skewed", lease_token=lease["lease_token"],
    )
    await campaigns.assert_active_lease(database, campaign_id, lease["lease_token"])


@pytest.mark.asyncio
async def test_expired_campaign_cannot_renew_inside_an_older_transaction(database):
    args = await create_baseline(database, uuid4())
    campaign_id = await create_campaign(database, args)
    lease = await campaigns.acquire_lease(database, campaign_id, worker_id="expired", ttl_s=60)
    await database.execute(
        "UPDATE evolution_campaigns SET lease_expires_at=clock_timestamp() WHERE campaign_id=%s",
        (campaign_id,),
    )
    assert not await campaigns.renew_lease(
        database, campaign_id, worker_id="expired", lease_token=lease["lease_token"],
    )
    with pytest.raises(RuntimeError, match="fencing token was lost"):
        await campaigns.assert_active_lease(database, campaign_id, lease["lease_token"])


@pytest.mark.asyncio
async def test_expired_attempt_cannot_finalize_inside_an_older_transaction(database, monkeypatch):
    worker, _, snapshot = await ready_loop(database)

    @asynccontextmanager
    async def connection():
        yield database

    monkeypatch.setattr(loop_campaign, "get_conn", connection)
    monkeypatch.setattr(loop_campaign, "fetch_event_snapshot", AsyncMock(return_value=snapshot))
    campaign_id = await loop_campaign.handoff_campaign(
        worker, EvolverSettings(EVENT_EVOLUTION_ENABLED=True),
    )
    owner = worker["owner_account_id"]
    lease = await campaigns.acquire_lease(database, campaign_id, worker_id="holdout", ttl_s=60)
    current = await campaigns.get_campaign(database, campaign_id, owner)
    hypothesis = current["hypotheses"][0]
    compiled = compile_hypothesis(HypothesisSpec.model_validate(hypothesis["spec"]))
    implementation = await campaigns.insert_implementation(
        database, campaign_id=campaign_id, hypothesis_id=hypothesis["hypothesis_id"],
        generation=1, profile="direct", source_code=compiled.source_code,
        source_hash=compiled.source_hash, lease_token=lease["lease_token"],
    )
    await database.execute(
        "UPDATE evolution_campaigns SET status='holdout_ready',locked_candidate_id=%s WHERE campaign_id=%s",
        (implementation["implementation_id"], campaign_id),
    )
    attempt = await campaigns.reserve_holdout_attempt(database, campaign_id, owner, lease["lease_token"])
    claimed = await campaigns.claim_holdout_attempt(database, attempt["attempt_id"], owner, ttl_s=60)
    await database.execute(
        "UPDATE evolution_holdout_attempts SET lease_expires_at=clock_timestamp() WHERE attempt_id=%s",
        (attempt["attempt_id"],),
    )
    assert await campaigns.finalize_holdout_attempt(
        database, attempt["attempt_id"], owner, fencing_token=claimed["fencing_token"],
        passed=True, evidence={"test": "expired worker"},
    ) is None
    resumed = await campaigns.claim_holdout_attempt(database, attempt["attempt_id"], owner, ttl_s=60)
    assert resumed["attempt_id"] == attempt["attempt_id"]
    assert resumed["candidate_id"] == implementation["implementation_id"]
    assert resumed["fencing_token"] != claimed["fencing_token"]
