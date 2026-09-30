"""Proposal failures remain auditable without persisting untrusted provider content."""

import json
from uuid import uuid4

import pytest

from inalpha_evolver.hypothesis.proposer import propose_generation
from inalpha_evolver.hypothesis.seeding import seed_generation_one
from inalpha_evolver.mutator import Mutator
from inalpha_evolver.storage import campaigns, proposal_checkpoints

from .test_event_evolution import _ProposalClient
from .test_loop_storage import create_baseline, create_campaign, loop_database  # noqa: F401


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("secret-provider-content", {"code": "invalid_json_array", "kind": "missing_array"}),
        ("[]", {"code": "wrong_batch_size"}),
        ("[null,null,null,null]", {"code": "entry_not_object", "slot": 0}),
        (
            json.dumps([{"risk": {"position_pct": "secret-provider-content"}}] * 4),
            {
                "code": "dsl_validation_failed",
                "slot": 0,
                "fields": ["risk"],
                "violations": ["float_parsing"],
            },
        ),
    ],
)
async def test_rejection_categories_never_include_model_content(content, expected):
    scaffolds = seed_generation_one({"facts": []}, "BTC", "asset:BTC")
    result = await propose_generation(
        Mutator(llm_client=_ProposalClient(content)),
        generation=1,
        scaffolds=scaffolds,
        feedback=[],
        frozen_facts=[],
    )
    assert result.fallback_calls == 2
    assert result.diagnostics == ({"batch": 0, **expected}, {"batch": 1, **expected})
    assert "secret-provider-content" not in json.dumps(result.diagnostics)
    assert result.hypotheses == tuple(scaffolds)


@pytest.mark.asyncio
async def test_provider_receives_exact_nested_contract_and_mutable_fields_only():
    client = _ProposalClient("[{},{},{},{}]")
    result = await propose_generation(
        Mutator(llm_client=client),
        generation=1,
        scaffolds=seed_generation_one({"facts": []}, "BTC", "asset:BTC"),
        feedback=[],
        frozen_facts=[],
    )
    assert result.diagnostics == ()
    contract = json.loads(client.requests[0].user_prompt)["output_contract"]
    assert contract["minItems"] == contract["maxItems"] == 4
    assert contract["items"]["additionalProperties"] is False
    assert "evidence_ids" not in contract["items"]["properties"]
    from inalpha_evolver.hypothesis.models import _EVENT_TYPES

    assert set(contract["items"]["properties"]["event_types"]["items"]["enum"]) == _EVENT_TYPES
    confirmation = contract["$defs"]["ConfirmationSpec"]
    assert confirmation["additionalProperties"] is False
    assert confirmation["properties"]["lookback_bars"]["minimum"] == 2


@pytest.mark.asyncio
async def test_diagnostics_commit_and_restore_with_proposal_receipt(database):
    args = await create_baseline(database, uuid4())
    campaign_id = await create_campaign(database, args)
    lease = await campaigns.acquire_lease(database, campaign_id, worker_id="diagnostics", ttl_s=60)
    await campaigns.transition(
        database,
        campaign_id,
        args["owner_account_id"],
        from_statuses=("draft",),
        to_status="replaying",
        values={"active_generation": 1},
        lease_token=lease["lease_token"],
    )
    diagnostics = ({"batch": 0, "code": "dsl_validation_failed", "fields": ["risk"]},)
    scope = dict(
        campaign_id=campaign_id,
        generation=1,
        hypotheses=seed_generation_one({"facts": []}, "BTC", "asset:BTC"),
        lease_token=lease["lease_token"],
        cost_usd=0.1,
        fallback_calls=1,
        diagnostics=diagnostics,
    )
    assert await proposal_checkpoints.commit_proposal(database, **scope)
    assert not await proposal_checkpoints.commit_proposal(database, **scope)
    restored = await proposal_checkpoints.get_proposal(database, campaign_id, 1)
    assert restored["diagnostics"] == list(diagnostics)
    with pytest.raises(RuntimeError, match="lease"):
        await proposal_checkpoints.commit_proposal(database, **{**scope, "lease_token": uuid4()})


@pytest.mark.asyncio
async def test_input_budget_rejection_has_no_provider_call():
    client = _ProposalClient("[{},{},{},{}]")
    result = await propose_generation(
        Mutator(llm_client=client, max_input_utf8_bytes=1),
        generation=1,
        scaffolds=seed_generation_one({"facts": []}, "BTC", "asset:BTC"),
        feedback=[],
        frozen_facts=[],
    )
    assert client.calls == 0
    assert result.cost_usd == 0
    assert result.diagnostics == (
        {"batch": 0, "code": "input_budget_exceeded"},
        {"batch": 1, "code": "input_budget_exceeded"},
    )


@pytest.mark.asyncio
async def test_timeout_diagnostic_does_not_persist_exception_text():
    class Client:
        async def mutate(self, request):
            raise TimeoutError("secret-key-and-provider-response")

    result = await propose_generation(
        Mutator(llm_client=Client()),
        generation=1,
        scaffolds=seed_generation_one({"facts": []}, "BTC", "asset:BTC"),
        feedback=[],
        frozen_facts=[],
    )
    assert result.diagnostics == (
        {"batch": 0, "code": "provider_timeout"},
        {"batch": 1, "code": "provider_timeout"},
    )
    assert "secret" not in json.dumps(result.diagnostics)
