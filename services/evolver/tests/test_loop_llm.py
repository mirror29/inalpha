"""Loop-owned model calls reserve cost before sending and never refund unknown usage."""

from contextlib import asynccontextmanager
from decimal import Decimal
from uuid import uuid4

import pytest
from inalpha_shared_llm.types import CacheMetrics, MutationRequest, MutationResponse

from inalpha_evolver import loop_llm
from inalpha_evolver.storage import loop_authorizations as auth
from inalpha_evolver.storage import loop_dispatch, loops

from .llm_snapshot_fixtures import llm_snapshot
from .test_loop_storage import create_baseline, loop_database  # noqa: F401


@pytest.mark.asyncio
@pytest.mark.parametrize("lost_response", [False, True])
async def test_provider_attempts_are_bounded_by_persisted_budget(database, monkeypatch, lost_response):
    args = await create_baseline(database, uuid4())
    loop = await loops.ensure_for_e1_run(database, **args)
    await auth.register(
        database, loop_id=loop["loop_id"], owner_account_id=args["owner_account_id"],
        request_digest="a" * 64, max_cost_usd=Decimal("0.0170304"),
    )
    worker = await loop_dispatch.claim_next(database, worker_id="model-worker")
    scope = loop_llm.LoopModelScope(
        loop_id=loop["loop_id"], owner_account_id=args["owner_account_id"],
        lease_token=worker["lease_token"], phase="baseline",
    )

    @asynccontextmanager
    async def connection():
        yield database

    monkeypatch.setattr(loop_llm, "get_conn", connection)

    class Provider:
        calls = 0

        async def mutate(self, request):
            self.calls += 1
            if lost_response:
                raise TimeoutError("response lost")
            return MutationResponse(
                content="[]", cache_metrics=CacheMetrics(input_tokens=100, output_tokens=40)
            )

    provider = Provider()
    client = loop_llm.BudgetedLoopClient(provider, scope, llm_snapshot()["pricing"])
    request = MutationRequest(system_prompt="system", user_prompt="user")
    if lost_response:
        with pytest.raises(TimeoutError):
            await client.mutate(request)
    else:
        assert (await client.mutate(request)).content == "[]"
    with pytest.raises(auth.LoopBudgetExhausted):
        await client.mutate(request)
    assert provider.calls == 1
    ledger = await auth.register(
        database, loop_id=scope.loop_id, owner_account_id=scope.owner_account_id,
        request_digest="a" * 64, max_cost_usd=Decimal("0.0170304"),
    )
    assert ledger["reserved_usd"] == (Decimal("0.0170304") if lost_response else 0)
    assert ledger["spent_usd"] == (0 if lost_response else Decimal("0.000078"))


@pytest.mark.asyncio
async def test_budget_exhaustion_cannot_be_hidden_by_proposer_fallback() -> None:
    from inalpha_evolver.hypothesis.proposer import propose_generation
    from inalpha_evolver.mutator import Mutator

    from .test_event_evolution import _spec

    class ExhaustedProvider:
        async def mutate(self, request):
            raise auth.LoopBudgetExhausted("budget exhausted")

    mutator = Mutator(llm_client=ExhaustedProvider())
    with pytest.raises(auth.LoopBudgetExhausted):
        await mutator.mutate("class Strategy: pass")
    with pytest.raises(auth.LoopBudgetExhausted):
        await propose_generation(
            mutator, generation=1, scaffolds=[_spec() for _ in range(8)],
            feedback=[], frozen_facts=[],
        )
