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
        await preflight.preflight_run(
            _request(), SimpleNamespace(user_id=str(uuid4())), "Bearer owner-token"
        )
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
    monkeypatch.setattr(
        preflight,
        "resolve_seed",
        AsyncMock(return_value=SimpleNamespace(reference="sma_cross_v1", source_hash="a" * 64)),
    )
    response = await preflight.preflight_run(
        body, SimpleNamespace(user_id=str(uuid4())), "Bearer owner-token"
    )
    assert response.dataset_manifest.bar_count == 3
    assert response.dataset_manifest.requested_from == body.config.from_ts
    assert (
        response.estimated_max_cost_usd
        == body.budget * body.llm.pricing.estimated_max_usd_per_candidate
    )
    body.preparation = preflight.EvolutionPreparation(
        seed_source_hash=response.seed_source_hash,
        dataset_content_sha256=response.dataset_manifest.content_sha256,
    )
    assert response.request_digest == preflight.approval_request_digest(body)


@pytest.mark.asyncio
async def test_loop_preflight_checks_owner_before_network(preparation, monkeypatch):
    from .test_loop_start import make_request

    monkeypatch.setattr(preflight, "_require_event_evolution", lambda: None)
    target = AsyncMock(side_effect=NotFoundError("target not found"))
    fetch = AsyncMock()
    monkeypatch.setattr(preflight, "_validate_target_seed", target)
    monkeypatch.setattr(preflight, "fetch_event_snapshot", fetch)
    with pytest.raises(NotFoundError):
        await preflight.preflight_loop(
            make_request(uuid4()), SimpleNamespace(user_id=str(uuid4())), "Bearer owner-token"
        )
    fetch.assert_not_awaited()
    preparation[1].assert_not_awaited()


@pytest.mark.asyncio
async def test_loop_preflight_returns_structured_blocker_without_model_call(
    preparation, monkeypatch
):
    from .test_event_coverage import dataset
    from .test_loop_start import make_request

    body = make_request(uuid4())
    monkeypatch.setattr(preflight, "_require_event_evolution", lambda: None)
    monkeypatch.setattr(preflight, "_validate_target_seed", AsyncMock())
    fact = {
        "fact_id": "one",
        "event_type": "upgrade",
        "assets": ["BTC"],
        "available_at": "2026-01-01T00:00:00Z",
        "effective_at": "2026-01-01T00:00:00Z",
    }
    monkeypatch.setattr(
        preflight,
        "fetch_event_snapshot",
        AsyncMock(
            return_value={
                "fact_count": 1,
                "cutoff": body.campaign.config.as_of.isoformat(),
                "facts": [fact],
                "events_sha256": "b" * 64,
            }
        ),
    )
    preparation[1].side_effect = None
    preparation[1].return_value = dataset()
    result = await preflight.preflight_loop(
        body, SimpleNamespace(user_id=str(uuid4())), "Bearer owner-token"
    )
    assert result["status"] == "blocked"
    assert result["selection_fact_count"] == 0
    assert result["model_calls"] == 0
    assert result["execution_authorized"] is False


@pytest.mark.asyncio
async def test_loop_preflight_http_requires_authentication():
    import httpx
    from fastapi import FastAPI
    from inalpha_shared.auth import get_settings
    from inalpha_shared.middleware import install_error_handler

    from inalpha_evolver.api.routes import router

    from .test_loop_start import make_request

    app = FastAPI()
    app.dependency_overrides[get_settings] = lambda: SimpleNamespace(
        jwt_secret="test-only-32-character-secret-value", jwt_algorithm="HS256"
    )
    install_error_handler(app)
    app.include_router(router)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/v1/evolution-loops/preflight", json=make_request(uuid4()).model_dump(mode="json")
        )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_loop_preflight_http_preserves_snapshot_owner_rejection(preparation, monkeypatch):
    import httpx
    from fastapi import FastAPI
    from inalpha_shared.auth import User, get_current_user
    from inalpha_shared.middleware import install_error_handler

    from inalpha_evolver.api.routes import router

    from .test_loop_start import make_request

    owner = uuid4()
    app = FastAPI()
    install_error_handler(app)
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: User(user_id=str(owner))
    monkeypatch.setattr(preflight, "_require_event_evolution", lambda: None)
    monkeypatch.setattr(preflight, "_validate_target_seed", AsyncMock())
    fetch = AsyncMock(side_effect=NotFoundError("snapshot not found"))
    monkeypatch.setattr(preflight, "fetch_event_snapshot", fetch)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/v1/evolution-loops/preflight",
            json=make_request(uuid4()).model_dump(mode="json"),
            headers={"Authorization": "Bearer owner-token"},
        )
    assert response.status_code == 404
    assert fetch.call_args.kwargs["owner_account_id"] == owner
    preparation[1].assert_not_awaited()


@pytest.mark.asyncio
async def test_model_free_preparation_rejects_foreign_target_before_snapshot(preparation, monkeypatch):
    from .test_loop_start import make_request

    body = make_request(uuid4())
    request = preflight.ExperimentPreparationRequest(
        target_kind=body.campaign.target_kind, target_id=body.campaign.target_id,
        seed_strategy_id=body.baseline.seed_strategy_id,
        event_snapshot_id=body.campaign.event_snapshot_id,
        event_asset_code=body.campaign.config.event_asset_code, config=body.baseline.config,
    )
    monkeypatch.setattr(preflight, "_require_event_evolution", lambda: None)
    monkeypatch.setattr(preflight, "validate_target_seed", AsyncMock(side_effect=NotFoundError("target not found")))
    fetch = AsyncMock()
    monkeypatch.setattr(preflight, "fetch_event_snapshot", fetch)
    with pytest.raises(NotFoundError):
        await preflight.prepare_experiment(request, SimpleNamespace(user_id=str(uuid4())), "Bearer owner-token")
    fetch.assert_not_awaited()
    preparation[1].assert_not_awaited()


@pytest.mark.asyncio
async def test_model_free_preparation_reports_frozen_dataset_without_llm(preparation, monkeypatch):
    from .test_event_coverage import dataset
    from .test_loop_start import make_request

    body = make_request(uuid4())
    request = preflight.ExperimentPreparationRequest(
        target_kind=body.campaign.target_kind, target_id=body.campaign.target_id,
        seed_strategy_id=body.baseline.seed_strategy_id, event_snapshot_id=body.campaign.event_snapshot_id,
        event_asset_code=body.campaign.config.event_asset_code, config=body.baseline.config,
    )
    monkeypatch.setattr(preflight, "_require_event_evolution", lambda: None)
    monkeypatch.setattr(preflight, "validate_target_seed", AsyncMock())
    monkeypatch.setattr(preflight, "fetch_event_snapshot", AsyncMock(return_value={
        "cutoff": body.baseline.config.as_of.isoformat(), "facts": [], "events_sha256": "b" * 64,
    }))
    preparation[1].side_effect = None
    preparation[1].return_value = dataset()
    result = await preflight.prepare_experiment(request, SimpleNamespace(user_id=str(uuid4())), "Bearer owner-token")
    assert result["status"] == "blocked"
    assert result["model_calls"] == 0
    assert result["execution_authorized"] is False
    assert result["dataset_content_sha256"] == dataset().manifest.content_sha256
    assert result["seed_source_hash"] == "a" * 64
    assert "llm" not in request.model_dump()
