"""Signed HTTP launch of a loop and its initial E1 record in one database transaction."""

import base64
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from fastapi import FastAPI
from inalpha_paper.storage.backtest_runs import insert_run
from inalpha_shared.auth import User, get_current_user
from inalpha_shared.db import _db_dep
from inalpha_shared.middleware import install_error_handler

from inalpha_evolver.api import campaign_routes, loop_start
from inalpha_evolver.config import EvolverSettings
from inalpha_evolver.storage import loops, run_queries, runs

from .llm_snapshot_fixtures import llm_snapshot
from .test_loop_storage import loop_database  # noqa: F401


def make_request(target_id):
    config = {
        "venue": "binance",
        "symbol": "BTCUSDT",
        "timeframe": "1h",
        "from_ts": "2026-01-01T00:00:00Z",
        "as_of": "2026-02-01T00:00:00Z",
        "trading_mode": "perp",
    }
    return loop_start.StartEvolutionLoopRequest.model_validate(
        {
            "baseline": {
                "seed_strategy_id": "sma_cross_v1",
                "config": config,
                "llm": llm_snapshot(),
            },
            "campaign": {
                "event_snapshot_id": str(uuid4()),
                "target_kind": "backtest_run",
                "target_id": str(target_id),
                "config": {**config, "asset_id": "asset:BTC", "event_asset_code": "BTC"},
                "llm": llm_snapshot(),
            },
            "max_cost_usd": 1,
        }
    )


@pytest.mark.asyncio
async def test_signed_launch_is_atomic_repeatable_and_does_not_start_trading(database, monkeypatch):
    owner = uuid4()
    target_id = await insert_run(
        database, strategy_code="sma_cross", config={}, metrics={}, account_id=str(owner)
    )
    body = make_request(target_id)
    private_key = Ed25519PrivateKey.generate()
    settings = EvolverSettings(
        EVENT_EVOLUTION_ENABLED=True,
        EVOLUTION_CREDENTIAL_PUBLIC_KEY_B64=base64.b64encode(
            private_key.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
        ).decode(),
    )
    monkeypatch.setattr(loop_start, "get_evolver_settings", lambda: settings)
    monkeypatch.setattr(campaign_routes, "get_evolver_settings", lambda: settings)
    policy_reader = AsyncMock(return_value={
        "version": "paper-protection-v1", "protective_stop_loss_pct": 0.2,
    })
    monkeypatch.setattr(loop_start, "fetch_execution_policy", policy_reader)
    monkeypatch.setattr(
        loop_start,
        "fetch_event_snapshot",
        AsyncMock(
            return_value={
                "fact_count": 8,
                "cutoff": "2026-02-01T00:00:00Z",
                "asset_ids": ["asset:BTC"],
            }
        ),
    )
    app = FastAPI()
    install_error_handler(app)
    app.include_router(loop_start.router)
    app.state.loop_manager = SimpleNamespace(healthy=True, notify_async=AsyncMock())

    async def connection():
        yield database

    app.dependency_overrides[_db_dep] = connection
    app.dependency_overrides[get_current_user] = lambda: User(user_id=str(owner))
    operation = str(uuid4())
    now = int(time.time())
    token = jwt.encode(
        {
            "sub": str(owner),
            "aud": "inalpha-evolver",
            "iat": now,
            "exp": now + 300,
            "jti": str(uuid4()),
            "token_use": "evolution_credential",
            "grant_purpose": "evolution_loop_start",
            "operation_id": operation,
            "config_id": body.baseline.llm.config_id,
            "provider": body.baseline.llm.provider,
            "llm_config_digest": body.baseline.llm.config_digest,
            "request_digest": loop_start.loop_request_digest(body),
        },
        private_key,
        algorithm="EdDSA",
    )
    headers = {"Idempotency-Key": operation, "X-Evolution-Credential": token}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://evolver.test"
    ) as client:
        first = await client.post(
            "/evolution-loops/start", json=body.model_dump(mode="json"), headers=headers
        )
        assert first.status_code == 202, first.text
        repeated = await client.post(
            "/evolution-loops/start", json=body.model_dump(mode="json"), headers=headers
        )
        assert repeated.status_code == 202, repeated.text
        assert first.json()["loop_id"] == repeated.json()["loop_id"]
        assert first.json()["campaign_id"] is None
        assert first.json()["status"] == "target_resolved"
        altered = {**body.model_dump(mode="json"), "max_cost_usd": 2}
        assert (
            await client.post("/evolution-loops/start", json=altered, headers=headers)
        ).status_code == 403
    run = await runs.get_run(database, first.json()["e1_run_id"], owner)
    assert run["status"] == "queued"
    assert run["config"]["trading_mode"] == "perp"
    assert run["config"]["protection_policy"]["protective_stop_loss_pct"] == 0.2
    assert first.json()["frozen_config"]["protection_policy"] == run["config"]["protection_policy"]
    policy_reader.assert_awaited_once()
    assert len(await loops.list_loops(database, owner, limit=20)) == 1
    assert await loops.list_loops(database, uuid4(), limit=20) == []
    assert await run_queries.claim_next(database) is None
    from inalpha_evolver.loop_fencing import BaselineLease, baseline_lease
    from inalpha_evolver.storage import loop_dispatch

    worker = await loop_dispatch.claim_next(database, worker_id="launch-test")
    with baseline_lease(BaselineLease(worker["loop_id"], owner, run["run_id"], worker["lease_token"])):
        await runs.transition(database, run["run_id"], from_statuses=("queued",), to_status="running")
    await run_queries.reconcile_interrupted(database)
    restored = await runs.get_run(database, run["run_id"], owner)
    assert restored["status"] == "running"


def test_loop_request_rejects_different_markets_or_models():
    body = make_request(uuid4()).model_dump(mode="json")
    body["campaign"]["config"]["trading_mode"] = "spot"
    with pytest.raises(ValueError, match="configuration mismatch"):
        loop_start.StartEvolutionLoopRequest.model_validate(body)


def test_loop_request_rejects_different_funding_costs():
    body = make_request(uuid4()).model_dump(mode="json")
    body["baseline"]["config"]["funding_rate"] = 0.001
    with pytest.raises(ValueError, match="configuration mismatch: funding_rate"):
        loop_start.StartEvolutionLoopRequest.model_validate(body)
