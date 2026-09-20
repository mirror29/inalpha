"""Real signed Paper handoff with controlled outcomes, not live performance evidence."""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from inalpha_paper.api.evolution_forward import router
from inalpha_shared.config import Settings, get_settings
from inalpha_shared.db import _db_dep

from inalpha_evolver import forward_client
from inalpha_evolver.config import EvolverSettings
from inalpha_evolver.hypothesis.compiler import compile_hypothesis
from inalpha_evolver.hypothesis.models import HypothesisSpec
from inalpha_evolver.runtime import campaign as runtime
from inalpha_evolver.runtime import loop_campaign
from inalpha_evolver.storage import campaigns, loops

from .test_loop_handoff import ready_loop
from .test_loop_storage import loop_database  # noqa: F401


@pytest.mark.asyncio
@pytest.mark.parametrize("passed,terminal", [(True, "adoption_ready"), (False, "rejected")])
async def test_signed_forward_automatically_consumes_one_holdout(database, monkeypatch, passed, terminal):
    worker, _, snapshot = await ready_loop(database)
    owner = worker["owner_account_id"]

    @asynccontextmanager
    async def connection():
        yield database

    monkeypatch.setattr(loop_campaign, "get_conn", connection)
    monkeypatch.setattr(runtime, "get_conn", connection)
    monkeypatch.setattr(loop_campaign, "fetch_event_snapshot", AsyncMock(return_value=snapshot))
    secret = "forward-holdout-integration-at-least-32-bytes"
    settings = EvolverSettings(EVENT_EVOLUTION_ENABLED=True, JWT_SECRET=secret)
    campaign_id = await loop_campaign.handoff_campaign(worker, settings)
    lease = await campaigns.acquire_lease(database, campaign_id, worker_id="transition", ttl_s=60)
    current = await campaigns.get_campaign(database, campaign_id, owner)
    hypothesis = current["hypotheses"][0]
    compiled = compile_hypothesis(HypothesisSpec.model_validate(hypothesis["spec"]))
    implementation = await campaigns.insert_implementation(
        database, campaign_id=campaign_id, hypothesis_id=hypothesis["hypothesis_id"],
        generation=1, profile="direct", source_code=compiled.source_code,
        source_hash=compiled.source_hash, lease_token=lease["lease_token"],
    )
    await database.execute(
        "UPDATE evolution_campaigns SET status='candidate_locked',locked_candidate_id=%s WHERE campaign_id=%s",
        (implementation["implementation_id"], campaign_id),
    )
    app = FastAPI()
    app.include_router(router)

    async def dependency():
        yield database

    app.dependency_overrides[_db_dep] = dependency
    app.dependency_overrides[get_settings] = lambda: Settings(JWT_SECRET=secret)
    original_client = httpx.AsyncClient
    monkeypatch.setattr(forward_client.httpx, "AsyncClient", lambda **kwargs: original_client(
        **kwargs, transport=httpx.ASGITransport(app=app),
    ))
    current = await campaigns.get_campaign(database, campaign_id, owner)
    await runtime.execute_campaign(current, settings)
    current = await campaigns.get_campaign(database, campaign_id, owner)
    assert current["status"] == "waiting_forward"
    # Deliberate fixture: test the transition, not 30-day collection or statistical fitness.
    await database.execute(
        """UPDATE paper_evolution_forward_sandboxes SET status='passed',event_count=3,
finished_at=clock_timestamp(),evidence_version=1 WHERE sandbox_id=%s""",
        (current["forward_sandbox_id"],),
    )
    await runtime.execute_campaign(current, settings)
    current = await campaigns.get_campaign(database, campaign_id, owner)
    assert current["status"] == "holdout_ready"
    assert current["forward_metrics"]["paper_forward"]["evidence_digest"]
    evaluator = AsyncMock(return_value=(passed, {"fixture": "controlled holdout outcome"}))
    monkeypatch.setattr(runtime, "evaluate_sealed_holdout", evaluator)
    await runtime.execute_campaign(current, settings)
    first = await loops.get_loop(database, worker["loop_id"], owner)
    assert first["status"] == terminal
    assert first["holdout_attempt_id"] is not None
    await runtime.execute_campaign(current, settings)
    repeated = await loops.get_loop(database, worker["loop_id"], owner)
    assert repeated["holdout_attempt_id"] == first["holdout_attempt_id"]
    assert repeated["status"] == terminal
    evaluator.assert_awaited_once()
