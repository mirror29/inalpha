"""Real Paper JWT, HTTP route, PostgreSQL and Evolver handoff across a lost response."""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from inalpha_paper.api.evolution_forward import router
from inalpha_paper.storage import evolution_forward as paper_store
from inalpha_shared.config import Settings, get_settings
from inalpha_shared.db import _db_dep
from inalpha_shared.middleware import install_error_handler

from inalpha_evolver import forward_client
from inalpha_evolver.config import EvolverSettings
from inalpha_evolver.hypothesis.compiler import compile_hypothesis
from inalpha_evolver.hypothesis.models import HypothesisSpec
from inalpha_evolver.runtime import campaign as runtime
from inalpha_evolver.runtime import loop_campaign
from inalpha_evolver.storage import campaigns

from .test_loop_handoff import ready_loop
from .test_loop_storage import loop_database  # noqa: F401


@pytest.mark.asyncio
async def test_lost_paper_response_reuses_sandbox_before_advancing_forward(database, monkeypatch):
    worker, _, snapshot = await ready_loop(database)

    @asynccontextmanager
    async def connection():
        yield database

    monkeypatch.setattr(loop_campaign, "get_conn", connection)
    monkeypatch.setattr(runtime, "get_conn", connection)
    monkeypatch.setattr(loop_campaign, "fetch_event_snapshot", AsyncMock(return_value=snapshot))
    secret = "forward-integration-service-secret-at-least-32-bytes"
    settings = EvolverSettings(EVENT_EVOLUTION_ENABLED=True, JWT_SECRET=secret)
    campaign_id = await loop_campaign.handoff_campaign(worker, settings)
    lease = await campaigns.acquire_lease(database, campaign_id, worker_id="handoff", ttl_s=60)
    current = await campaigns.get_campaign(database, campaign_id, worker["owner_account_id"])
    hypothesis = current["hypotheses"][0]
    compiled = compile_hypothesis(HypothesisSpec.model_validate(hypothesis["spec"]))
    implementation = await campaigns.insert_implementation(
        database, campaign_id=campaign_id, hypothesis_id=hypothesis["hypothesis_id"],
        generation=1, profile="direct", source_code=compiled.source_code,
        source_hash=compiled.source_hash, lease_token=lease["lease_token"],
    )
    # Fixture starts after selection; this test does not claim to validate the champion's fitness.
    await database.execute(
        "UPDATE evolution_campaigns SET status='candidate_locked',locked_candidate_id=%s WHERE campaign_id=%s",
        (implementation["implementation_id"], campaign_id),
    )
    current = await campaigns.get_campaign(database, campaign_id, worker["owner_account_id"])
    app = FastAPI()
    install_error_handler(app)
    app.include_router(router)

    async def dependency():
        yield database

    app.dependency_overrides[_db_dep] = dependency
    app.dependency_overrides[get_settings] = lambda: Settings(JWT_SECRET=secret)
    original_client = httpx.AsyncClient
    monkeypatch.setattr(forward_client.httpx, "AsyncClient", lambda **kwargs: original_client(
        **kwargs, transport=httpx.ASGITransport(app=app),
    ))
    created = []

    async def lose_response(campaign, settings):
        result = await forward_client.create_forward_sandbox(campaign, settings)
        created.append(result)
        raise httpx.ReadTimeout("response lost after Paper committed")

    monkeypatch.setattr(runtime, "create_forward_sandbox", lose_response)
    with pytest.raises(httpx.ReadTimeout):
        await runtime._ensure_forward_sandbox(current, settings)
    unchanged = await campaigns.get_campaign(database, campaign_id, worker["owner_account_id"])
    assert unchanged["status"] == "candidate_locked"
    assert unchanged["forward_sandbox_id"] is None
    sandbox_id = UUID(created[0]["sandbox_id"])
    assert await paper_store.get_sandbox(database, sandbox_id, uuid4()) is None

    monkeypatch.setattr(runtime, "create_forward_sandbox", forward_client.create_forward_sandbox)
    replacement_token = uuid4()
    await database.execute(
        "UPDATE evolution_campaigns SET lease_token=%s WHERE campaign_id=%s",
        (replacement_token, campaign_id),
    )
    with pytest.raises(RuntimeError, match="fencing token was lost"):
        await runtime._ensure_forward_sandbox(current, settings)
    current = await campaigns.get_campaign(database, campaign_id, worker["owner_account_id"])
    await runtime._ensure_forward_sandbox(current, settings)
    linked = await campaigns.get_campaign(database, campaign_id, worker["owner_account_id"])
    assert linked["status"] == "waiting_forward"
    assert linked["forward_sandbox_id"] == sandbox_id
    repeat = await forward_client.create_forward_sandbox(linked, settings)
    assert UUID(repeat["sandbox_id"]) == sandbox_id
    assert repeat["frozen_versions"]["protection_policy"]["protective_stop_loss_pct"] == 0.2
