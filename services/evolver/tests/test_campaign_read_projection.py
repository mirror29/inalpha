"""Dashboard campaign reads exclude executable source without breaking worker reads."""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest

from inalpha_evolver.config import EvolverSettings
from inalpha_evolver.runtime import loop_campaign
from inalpha_evolver.storage import campaigns

from .test_loop_handoff import ready_loop
from .test_loop_storage import loop_database  # noqa: F401


@pytest.mark.asyncio
async def test_source_free_read_preserves_metrics_and_internal_source(database, monkeypatch):
    worker, _, snapshot = await ready_loop(database)

    @asynccontextmanager
    async def connection():
        yield database

    monkeypatch.setattr(loop_campaign, "get_conn", connection)
    monkeypatch.setattr(loop_campaign, "fetch_event_snapshot", AsyncMock(return_value=snapshot))
    campaign_id = await loop_campaign.handoff_campaign(worker, EvolverSettings(EVENT_EVOLUTION_ENABLED=True))
    owner = worker["owner_account_id"]
    lease = await campaigns.acquire_lease(database, campaign_id, worker_id="projection", ttl_s=60)
    current = await campaigns.get_campaign(database, campaign_id, owner)
    await campaigns.insert_implementation(
        database, campaign_id=campaign_id, hypothesis_id=current["hypotheses"][0]["hypothesis_id"],
        generation=1, profile="direct", source_code="source-only-for-worker", source_hash="a" * 64,
        lease_token=lease["lease_token"],
    )
    public = await campaigns.get_campaign(database, campaign_id, owner, include_source=False)
    internal = await campaigns.get_campaign(database, campaign_id, owner)
    assert "source_code" not in public["implementations"][0]
    assert internal["implementations"][0]["source_code"] == "source-only-for-worker"
    assert public["generations"] == internal["generations"]
    assert public["implementations"][0] == {
        key: value for key, value in internal["implementations"][0].items() if key != "source_code"
    }
