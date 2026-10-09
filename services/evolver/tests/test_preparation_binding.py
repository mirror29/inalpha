"""Approved data identity is enforced before execution can exchange model credentials."""
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from inalpha_shared.errors import ConflictError

from inalpha_evolver.runtime import executor


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["source", "data"])
async def test_changed_prepared_input_never_reaches_model_execution(monkeypatch, changed):
    run = {
        "owner_account_id": uuid4(),
        "seed_source_hash": "a" * 64 if changed != "source" else "c" * 64,
        "config": {
            "venue": "binance", "symbol": "BTCUSDT", "timeframe": "1h",
            "from_ts": "2026-08-01T00:00:00Z", "as_of": "2026-08-02T00:00:00Z",
            "preparation": {"seed_source_hash": "a" * 64, "dataset_content_sha256": "b" * 64},
        },
    }
    dataset = SimpleNamespace(manifest=SimpleNamespace(
        content_sha256="b" * 64 if changed != "data" else "c" * 64))

    @asynccontextmanager
    async def client(url, token):
        yield object()

    execute = AsyncMock()
    monkeypatch.setattr(executor, "DataClient", client)
    monkeypatch.setattr(executor.FrozenBarsLoader, "load", AsyncMock(return_value=dataset))
    monkeypatch.setattr(executor, "execute_frozen_run", execute)
    settings = SimpleNamespace(
        data_service_url="http://data.test", service_token_ttl_s=3600,
        jwt_secret="test-preparation-secret-at-least-32-bytes", jwt_algorithm="HS256",
    )
    with pytest.raises(ConflictError) as error:
        await executor.execute_run(run, mutator=None, settings=settings)
    assert error.value.code == "EVOLUTION_PREPARATION_CHANGED"
    execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_paid_run_requires_preparation_even_with_a_verified_grant(monkeypatch):
    from fastapi import BackgroundTasks
    from inalpha_shared.errors import ValidationError

    from inalpha_evolver.api import run_routes

    from .test_api_contract import _request

    monkeypatch.setattr(run_routes, "verify_evolution_approval", lambda *args, **kwargs: None)
    with pytest.raises(ValidationError) as error:
        await run_routes.start_run(
            _request(), object(), BackgroundTasks(), None,
            SimpleNamespace(user_id=str(uuid4())), "approved-operation", "verified-test-grant",
        )
    assert error.value.code == "EVOLUTION_PREPARATION_REQUIRED"
