"""Preparation rejects invalid owner/data before any paid execution is created."""
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from inalpha_shared.errors import NotFoundError, ValidationError

from inalpha_evolver.api import preflight

from .test_api_contract import _request


@pytest.fixture
def preparation(monkeypatch):
    connection = object()
    released = False

    @asynccontextmanager
    async def conn():
        nonlocal released
        yield connection
        released = True

    client = SimpleNamespace()

    @asynccontextmanager
    async def data_client(url, token):
        assert released, "network preparation must not occupy a database connection"
        assert token == "owner-token"
        yield client

    seed = SimpleNamespace(reference="sma_cross_v1", source_hash="a" * 64)
    resolve = AsyncMock(return_value=seed)
    load = AsyncMock(side_effect=ValidationError("missing bars", code="EVOLUTION_DATA_GAP_INVALID"))
    monkeypatch.setattr(preflight, "get_conn", conn)
    monkeypatch.setattr(preflight, "resolve_seed", resolve)
    monkeypatch.setattr(preflight, "DataClient", data_client)
    monkeypatch.setattr(preflight.FrozenBarsLoader, "load", load)
    return resolve, load


@pytest.mark.asyncio
async def test_foreign_seed_fails_before_connector_preparation(preparation):
    resolve, load = preparation
    resolve.side_effect = NotFoundError("seed not found", code="EVOLUTION_SEED_NOT_FOUND")
    with pytest.raises(NotFoundError):
        await preflight.preflight_run(_request(), SimpleNamespace(user_id=str(uuid4())), "Bearer owner-token")
    load.assert_not_awaited()


@pytest.mark.asyncio
async def test_incomplete_data_is_not_a_successful_preflight(preparation):
    resolve, load = preparation
    user = SimpleNamespace(user_id=str(uuid4()))
    with pytest.raises(ValidationError) as error:
        await preflight.preflight_run(_request(), user, "Bearer owner-token")
    assert error.value.code == "EVOLUTION_DATA_GAP_INVALID"
    assert str(resolve.call_args.args[2]) == user.user_id
    load.assert_awaited_once()


@pytest.mark.asyncio
async def test_successful_preflight_reports_real_manifest_and_estimate_without_run(monkeypatch):
    from datetime import timedelta

    from .test_frozen_bars import FakeDataClient, _hourly_bars

    body = _request()
    body.config.from_ts = body.config.as_of - timedelta(hours=3)

    @asynccontextmanager
    async def conn():
        yield object()

    @asynccontextmanager
    async def data_client(url, token):
        yield FakeDataClient(_hourly_bars())

    monkeypatch.setattr(preflight, "get_conn", conn)
    monkeypatch.setattr(preflight, "DataClient", data_client)
    monkeypatch.setattr(preflight, "resolve_seed", AsyncMock(return_value=SimpleNamespace(
        reference="sma_cross_v1", source_hash="a" * 64)))
    response = await preflight.preflight_run(body, SimpleNamespace(user_id=str(uuid4())), "Bearer owner-token")
    assert response.dataset_manifest.bar_count == 3
    assert response.dataset_manifest.requested_from == body.config.from_ts
    assert response.estimated_max_cost_usd == body.budget * body.llm.pricing.estimated_max_usd_per_candidate
    body.preparation = preflight.EvolutionPreparation(
        seed_source_hash=response.seed_source_hash,
        dataset_content_sha256=response.dataset_manifest.content_sha256,
    )
    assert response.request_digest == preflight.approval_request_digest(body)
