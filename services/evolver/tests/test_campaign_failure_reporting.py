"""Campaign 输入预检与异步失败信息测试。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import httpx
import pytest
from inalpha_shared.errors import ValidationError

from inalpha_evolver.config import EvolverSettings
from inalpha_evolver.owner_llm import CredentialTemporarilyUnavailable
from inalpha_evolver.runtime import campaign as campaign_runtime
from inalpha_evolver.runtime import campaign_manager as manager_runtime


@pytest.mark.asyncio
async def test_campaign_validates_data_before_redeeming_llm_grant(monkeypatch) -> None:
    campaign = {
        "campaign_id": uuid4(),
        "owner_account_id": uuid4(),
        "event_snapshot_id": uuid4(),
        "frozen_config": {
            "venue": "binance",
            "symbol": "BTC/USDT:USDT",
            "asset_id": "asset:BTC",
            "event_asset_code": "BTC",
            "timeframe": "4h",
            "from_ts": datetime.now(UTC) - timedelta(days=30),
            "as_of": datetime.now(UTC) - timedelta(hours=1),
        },
    }
    redeemed = False

    async def fail_load(self, **kwargs: Any) -> None:
        raise ValidationError(
            "market data upstream unavailable",
            code="EVOLUTION_DATA_FRESHNESS_FAILED",
        )

    async def build_mutator(*args: Any, **kwargs: Any) -> None:
        nonlocal redeemed
        redeemed = True

    @asynccontextmanager
    async def fake_conn() -> AsyncIterator[object]:
        yield object()

    async def no_snapshot(*args: Any, **kwargs: Any) -> None:
        return None

    monkeypatch.setattr(campaign_runtime.FrozenBarsLoader, "load", fail_load)
    monkeypatch.setattr(campaign_runtime, "build_owner_mutator", build_mutator)
    monkeypatch.setattr(campaign_runtime, "get_conn", fake_conn)
    monkeypatch.setattr(campaign_runtime, "get_campaign_data_snapshot", no_snapshot)

    with pytest.raises(ValidationError, match="market data upstream unavailable"):
        await campaign_runtime.execute_campaign(campaign, EvolverSettings())

    assert redeemed is False


@pytest.mark.asyncio
async def test_campaign_manager_persists_taskgroup_leaf_error(monkeypatch) -> None:
    campaign = {
        "campaign_id": uuid4(),
        "owner_account_id": uuid4(),
        "lease_token": uuid4(),
    }
    captured: dict[str, Any] = {}
    transition: dict[str, Any] = {}

    async def fail_campaign(*args: Any, **kwargs: Any) -> None:
        raise ValidationError(
            "data-service backfill 502: market data upstream unavailable",
            code="EVOLUTION_DATA_FRESHNESS_FAILED",
        )

    @asynccontextmanager
    async def fake_conn() -> AsyncIterator[object]:
        yield object()

    async def capture_transition(*args: Any, **kwargs: Any) -> None:
        transition["to_status"] = kwargs["to_status"]
        captured.update(kwargs["values"])

    monkeypatch.setattr(manager_runtime, "execute_campaign", fail_campaign)
    monkeypatch.setattr(manager_runtime, "get_conn", fake_conn)
    monkeypatch.setattr(manager_runtime.store, "transition", capture_transition)
    settings = SimpleNamespace(campaign_max_concurrent=1, campaign_lease_ttl_s=90)

    await manager_runtime.CampaignManager(settings)._execute(campaign)  # type: ignore[arg-type]

    assert captured["failure_code"] == "EVOLUTION_DATA_FRESHNESS_FAILED"
    assert captured["failure_message"] == (
        "data-service backfill 502: market data upstream unavailable"
    )
    assert transition["to_status"] == "replaying"
    assert captured["finished_at"] is None
    assert captured["lease_token"] is None


@pytest.mark.asyncio
async def test_campaign_manager_requeues_temporary_credential_failure(monkeypatch) -> None:
    campaign = {
        "campaign_id": uuid4(),
        "owner_account_id": uuid4(),
        "lease_token": uuid4(),
    }
    transition: dict[str, Any] = {}

    async def fail_campaign(*args: Any, **kwargs: Any) -> None:
        raise CredentialTemporarilyUnavailable(
            "owner LLM credential service unavailable: ReadTimeout"
        )

    @asynccontextmanager
    async def fake_conn() -> AsyncIterator[object]:
        yield object()

    async def capture_transition(*args: Any, **kwargs: Any) -> None:
        transition["to_status"] = kwargs["to_status"]
        transition.update(kwargs["values"])

    monkeypatch.setattr(manager_runtime, "execute_campaign", fail_campaign)
    monkeypatch.setattr(manager_runtime, "get_conn", fake_conn)
    monkeypatch.setattr(manager_runtime.store, "transition", capture_transition)
    settings = SimpleNamespace(campaign_max_concurrent=1, campaign_lease_ttl_s=90)

    await manager_runtime.CampaignManager(settings)._execute(campaign)  # type: ignore[arg-type]

    assert transition["to_status"] == "replaying"
    assert transition["failure_code"] == "EVOLUTION_CREDENTIAL_UNAVAILABLE"
    assert transition["failure_message"].endswith("ReadTimeout")
    assert transition["lease_expires_at"] > datetime.now(UTC)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "phase", ["replaying", "candidate_locked", "waiting_forward", "holdout_ready"]
)
@pytest.mark.parametrize("status_code", [429, 503, 403])
async def test_retry_preserves_phase_and_fencing(monkeypatch, phase, status_code):
    campaign = {
        "campaign_id": uuid4(),
        "owner_account_id": uuid4(),
        "lease_token": uuid4(),
        "status": phase,
    }
    transitions = []

    async def fail_campaign(*args, **kwargs):
        response = httpx.Response(
            status_code, request=httpx.Request("GET", "http://paper.test/forward")
        )
        response.raise_for_status()

    @asynccontextmanager
    async def connection():
        yield object()

    async def transition(*args, **kwargs):
        transitions.append(kwargs)

    monkeypatch.setattr(manager_runtime, "execute_campaign", fail_campaign)
    monkeypatch.setattr(manager_runtime, "get_conn", connection)
    monkeypatch.setattr(manager_runtime.store, "transition", transition)
    settings = SimpleNamespace(campaign_max_concurrent=1, campaign_lease_ttl_s=90)
    await manager_runtime.CampaignManager(settings)._execute(campaign)

    assert len(transitions) == 1
    saved = transitions[0]
    assert saved["from_statuses"] == (phase,)
    assert saved["lease_token"] == campaign["lease_token"]
    if status_code == 403:
        assert saved["to_status"] == "failed"
        assert saved["values"]["finished_at"] is not None
    else:
        assert saved["to_status"] == phase
        assert saved["values"]["finished_at"] is None
        assert saved["values"]["lease_expires_at"] > datetime.now(UTC)


@pytest.mark.asyncio
async def test_campaign_dispatcher_recovers_health_after_database_poll(monkeypatch) -> None:
    """A successful empty poll clears a prior transient dispatcher failure."""
    settings = SimpleNamespace(campaign_max_concurrent=1, campaign_lease_ttl_s=90)
    manager = manager_runtime.CampaignManager(settings)  # type: ignore[arg-type]
    attempts = 0

    @asynccontextmanager
    async def fake_conn() -> AsyncIterator[object]:
        yield object()

    async def claim(*args: Any, **kwargs: Any) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("database temporarily unavailable")
        manager.closing = True
        return None

    async def no_delay(*args: Any, **kwargs: Any) -> None:
        return None

    monkeypatch.setattr(manager_runtime, "get_conn", fake_conn)
    monkeypatch.setattr(manager_runtime.store, "claim_next_campaign", claim)
    monkeypatch.setattr(manager_runtime.asyncio, "sleep", no_delay)
    monkeypatch.setattr(manager, "_wait", no_delay)

    await manager._dispatch()

    assert attempts == 2
    assert manager.unhealthy_reason is None
