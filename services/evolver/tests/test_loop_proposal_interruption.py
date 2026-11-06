"""Provider responses must survive interruption before generation proposal commit."""

import asyncio
from contextlib import asynccontextmanager

import pytest
from inalpha_shared_llm.types import CacheMetrics, MutationResponse

from inalpha_evolver.api.schemas import CampaignConfig
from inalpha_evolver.config import EvolverSettings
from inalpha_evolver.loop_llm import BudgetedLoopClient, campaign_model_scope
from inalpha_evolver.mutator import Mutator
from inalpha_evolver.runtime import campaign as runtime
from inalpha_evolver.runtime import loop_campaign
from inalpha_evolver.storage import campaigns, proposal_checkpoints

from .test_loop_handoff import ready_loop
from .test_loop_storage import loop_database  # noqa: F401
from .test_persistent_snapshot import _dataset


@pytest.mark.asyncio
async def test_resume_does_not_repurchase_returned_proposal_batches(database, monkeypatch):
    worker, _, snapshot = await ready_loop(database)
    snapshot["facts"][0].update(
        effective_at="2026-01-01T01:00:00Z", assets=["BTC"],
        asset_ids=["asset:BTC"], confidence=0.9,
    )
    lock = asyncio.Lock()

    @asynccontextmanager
    async def connection():
        async with lock:
            yield database

    async def fetch(*args, **kwargs):
        return snapshot

    from inalpha_evolver import loop_llm

    monkeypatch.setattr(loop_campaign, "get_conn", connection)
    monkeypatch.setattr(loop_campaign, "fetch_event_snapshot", fetch)
    monkeypatch.setattr(runtime, "get_conn", connection)
    monkeypatch.setattr(loop_llm, "get_conn", connection)
    settings = EvolverSettings(EVENT_EVOLUTION_ENABLED=True)
    campaign_id = await loop_campaign.handoff_campaign(worker, settings)
    leased = await campaigns.acquire_lease(database, campaign_id, worker_id="proposal-gap", ttl_s=600)
    scope = await campaign_model_scope(leased)

    class Provider:
        calls = 0

        async def mutate(self, request):
            self.calls += 1
            return MutationResponse(
                content="[{}, {}, {}, {}]", cache_metrics=CacheMetrics(input_tokens=100, output_tokens=40),
            )

    async def interrupted_commit(*args, **kwargs):
        raise RuntimeError("injected exit before generation checkpoint")

    monkeypatch.setattr(proposal_checkpoints, "commit_proposal", interrupted_commit)
    provider = Provider()
    config = CampaignConfig.model_validate({
        key: value for key, value in leased["frozen_config"].items() if key in CampaignConfig.model_fields
    })
    for _ in range(2):
        # Rebuild the client to ensure reuse cannot come from in-memory state.
        client = BudgetedLoopClient(provider, scope, leased["llm_snapshot"]["pricing"])
        with pytest.raises(RuntimeError, match="injected exit"):
            await runtime._execute_campaign(
                leased, settings, Mutator(llm_client=client), config=config,
                dataset=_dataset(), snapshot=snapshot,
            )
    assert provider.calls == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["unknown", "changed", "fenced", "known", "receipt_missing"])
async def test_proposal_response_checkpoint_enforces_authority_and_receipts(database, monkeypatch, mode):
    import json
    from decimal import Decimal
    from uuid import uuid4

    from inalpha_shared_llm.types import MutationRequest

    from inalpha_evolver import loop_llm
    from inalpha_evolver.storage import loop_authorizations as authority
    from inalpha_evolver.storage.proposal_responses import ProposalResponseUnconfirmed

    worker, _, snapshot = await ready_loop(database)

    @asynccontextmanager
    async def connection():
        yield database

    async def fetch(*args, **kwargs):
        return snapshot

    monkeypatch.setattr(loop_campaign, "get_conn", connection)
    monkeypatch.setattr(loop_campaign, "fetch_event_snapshot", fetch)
    monkeypatch.setattr(loop_llm, "get_conn", connection)
    campaign_id = await loop_campaign.handoff_campaign(worker, EvolverSettings(EVENT_EVOLUTION_ENABLED=True))
    leased = await campaigns.acquire_lease(database, campaign_id, worker_id="receipt-test", ttl_s=600)
    scope = await campaign_model_scope(leased)

    class Provider:
        calls = 0

        async def mutate(self, request):
            self.calls += 1
            if mode == "unknown":
                raise TimeoutError("offline response loss")
            if mode == "receipt_missing":
                return MutationResponse(content="[]", usage_known=False)
            return MutationResponse(content="[]", cache_metrics=CacheMetrics(input_tokens=100, output_tokens=40))

    provider = Provider()
    pricing = leased["llm_snapshot"]["pricing"]
    client = BudgetedLoopClient(provider, scope, pricing)
    request = MutationRequest(system_prompt="system", user_prompt=json.dumps({"slots": []}))
    if mode == "unknown":
        for _ in range(2):
            with pytest.raises(ProposalResponseUnconfirmed):
                await BudgetedLoopClient(provider, scope, pricing).mutate_proposal(request, generation=1, batch=0)
    else:
        original = await client.mutate_proposal(request, generation=1, batch=0)
        if mode == "changed":
            with pytest.raises(authority.LoopAuthorizationUnavailable, match="input changed"):
                await client.mutate_proposal(
                    MutationRequest(system_prompt="changed", user_prompt=request.user_prompt), generation=1, batch=0,
                )
        elif mode == "fenced":
            stale = loop_llm.LoopModelScope(scope.loop_id, scope.owner_account_id, uuid4(), "campaign")
            with pytest.raises(authority.LoopAuthorizationUnavailable):
                await BudgetedLoopClient(provider, stale, pricing).mutate_proposal(request, generation=1, batch=0)
        else:
            assert await BudgetedLoopClient(provider, scope, pricing).mutate_proposal(request, generation=1, batch=0) == original
    if mode == "unknown":
        from inalpha_evolver.hypothesis.proposer import propose_generation
        from inalpha_evolver.hypothesis.seeding import seed_generation_one

        with pytest.raises(ProposalResponseUnconfirmed):
            await propose_generation(
                Mutator(llm_client=BudgetedLoopClient(provider, scope, pricing)), generation=1,
                scaffolds=seed_generation_one({"facts": []}, "BTC", "asset:BTC"),
                feedback=[], frozen_facts=[],
            )
    assert provider.calls == 1
    summary = await authority.budget_summary(database, worker["loop_id"], worker["owner_account_id"])
    assert summary["spent_usd"] == (0 if mode in {"unknown", "receipt_missing"} else Decimal("0.000078"))
    assert summary["reserved_usd"] == (Decimal("0.0170304") if mode in {"unknown", "receipt_missing"} else 0)


def test_discovery_scaffold_identity_is_stable_and_campaign_scoped():
    from uuid import uuid4

    from inalpha_evolver.hypothesis.seeding import seed_generation_one

    campaign_id = uuid4()
    first = seed_generation_one({"facts": []}, "BTC", "asset:BTC", identity_namespace=campaign_id)
    same = seed_generation_one({"facts": []}, "BTC", "asset:BTC", identity_namespace=campaign_id)
    other = seed_generation_one({"facts": []}, "BTC", "asset:BTC", identity_namespace=uuid4())
    assert first == same
    assert len({item.hypothesis_id for item in first + other}) == 16
    assert first[5].parent_ids == [first[0].hypothesis_id]
