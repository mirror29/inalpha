"""Real engine, compiler, database, and selection; only the external model is offline."""

import asyncio
from contextlib import asynccontextmanager

import pytest
from inalpha_shared_llm.types import CacheMetrics, MutationResponse

from inalpha_evolver.api.schemas import CampaignConfig
from inalpha_evolver.config import EvolverSettings
from inalpha_evolver.hypothesis.models import HypothesisSpec
from inalpha_evolver.mutator import Mutator
from inalpha_evolver.runtime import campaign as runtime
from inalpha_evolver.runtime import loop_campaign
from inalpha_evolver.storage import campaigns, loops, proposal_checkpoints

from .test_loop_handoff import ready_loop
from .test_loop_storage import loop_database  # noqa: F401
from .test_persistent_snapshot import _dataset


@pytest.mark.asyncio
@pytest.mark.parametrize("resume_proposal", [False, True])
async def test_five_generations_reject_insufficient_evidence_with_real_engine(
    database, monkeypatch, resume_proposal
):
    worker, _, snapshot = await ready_loop(database)
    snapshot["facts"][0].update(
        {
            "effective_at": "2026-01-01T01:00:00Z",
            "assets": ["BTC"],
            "asset_ids": ["asset:BTC"],
            "confidence": 0.9,
        }
    )
    lock = asyncio.Lock()

    @asynccontextmanager
    async def connection():
        async with lock:
            yield database

    async def fetch(*args, **kwargs):
        return snapshot

    monkeypatch.setattr(loop_campaign, "get_conn", connection)
    monkeypatch.setattr(loop_campaign, "fetch_event_snapshot", fetch)
    monkeypatch.setattr(runtime, "get_conn", connection)
    settings = EvolverSettings(EVENT_EVOLUTION_ENABLED=True, EVOLVER_JOB_TIMEOUT_S=30)
    campaign_id = await loop_campaign.handoff_campaign(worker, settings)
    leased = await campaigns.acquire_lease(
        database, campaign_id, worker_id="engine-test", ttl_s=600
    )
    config = CampaignConfig.model_validate(
        {
            key: value
            for key, value in leased["frozen_config"].items()
            if key in CampaignConfig.model_fields
        }
    )

    class OfflineProvider:
        calls = 0

        async def mutate(self, request):
            self.calls += 1
            return MutationResponse(content="[{}, {}, {}, {}]", cache_metrics=CacheMetrics())

    provider = OfflineProvider()
    if resume_proposal:
        current = await campaigns.get_campaign(database, campaign_id, worker["owner_account_id"])
        await proposal_checkpoints.commit_proposal(
            database,
            campaign_id=campaign_id,
            generation=1,
            lease_token=leased["lease_token"],
            hypotheses=[
                HypothesisSpec.model_validate(row["spec"]) for row in current["hypotheses"]
            ],
            cost_usd=0,
            fallback_calls=0,
        )
    async with asyncio.timeout(240):
        await runtime._execute_campaign(
            leased,
            settings,
            Mutator(llm_client=provider),
            config=config,
            dataset=_dataset(),
            snapshot=snapshot,
        )
    result = await campaigns.get_campaign(database, campaign_id, worker["owner_account_id"])
    assert provider.calls == (8 if resume_proposal else 10)
    assert result["active_generation"] == 5
    assert len(result["hypotheses"]) == 40
    assert len(result["implementations"]) == 120
    assert all(item["outcome"] == "succeeded" for item in result["implementations"])
    assert result["status"] == "insufficient_evidence"
    assert result["locked_candidate_id"] is None
    assert result["forward_sandbox_id"] is None
    assert result["holdout_consumed_at"] is None
    projected = await loops.get_loop(database, worker["loop_id"], worker["owner_account_id"])
    assert projected["status"] == "insufficient_evidence"
