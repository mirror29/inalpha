"""Atomic baseline-to-campaign handoff on the real database, without a chat grant."""

import hashlib
import json
from contextlib import asynccontextmanager
from decimal import Decimal
from uuid import uuid4

import pytest

from inalpha_evolver.config import EvolverSettings
from inalpha_evolver.data.persistent_snapshot import discovery_dataset, persist_loop_data_snapshot
from inalpha_evolver.runtime import loop_campaign
from inalpha_evolver.storage import campaigns, loop_authorizations, loop_dispatch, loops

from .test_loop_start import make_request
from .test_loop_storage import create_baseline, loop_database  # noqa: F401
from .test_persistent_snapshot import _dataset


async def ready_loop(database):
    args = await create_baseline(database, uuid4())
    body = make_request(uuid4())
    args["target_kind"] = body.campaign.target_kind
    args["target_id"] = body.campaign.target_id
    args["frozen_config"] = {
        **body.baseline.config.model_dump(mode="json"),
        "campaign_request": body.campaign.model_dump(mode="json"),
        "protection_policy": {"version": "paper-protection-v1", "protective_stop_loss_pct": 0.2},
    }
    loop = await loops.ensure_for_e1_run(database, **args)
    await loop_authorizations.register(
        database, loop_id=loop["loop_id"], owner_account_id=args["owner_account_id"],
        request_digest="a" * 64, max_cost_usd=Decimal("1"),
    )
    worker = await loop_dispatch.claim_next(database, worker_id="handoff-test", ttl_s=60)
    frozen = await persist_loop_data_snapshot(
        database, loop_id=loop["loop_id"], owner_account_id=args["owner_account_id"],
        lease_token=worker["lease_token"], dataset=_dataset(),
    )
    report = {"sharpe": 0.5, "num_bars": 6, "num_trades": 0, "total_return_pct": 0}
    await database.execute(
        """UPDATE strategy_evo_runs SET status='completed',config=%s,
dataset_manifest=%s,seed_report_snapshot=%s,baseline_snapshot=%s WHERE run_id=%s""",
        (json.dumps({**body.baseline.config.model_dump(mode="json"), "protection_policy": args["frozen_config"]["protection_policy"]}),
         discovery_dataset(frozen).manifest.model_dump_json(), json.dumps(report),
         json.dumps(report), args["e1_run_id"]),
    )
    await loop_dispatch.complete_step(
        database, loop_id=loop["loop_id"], owner_account_id=args["owner_account_id"],
        lease_token=worker["lease_token"], step_key="baseline", output_id=args["e1_run_id"],
    )
    snapshot_id = body.campaign.event_snapshot_id
    await database.execute(
        """INSERT INTO market_event_snapshots(snapshot_id,cutoff,policy_version,query_hash,events_sha256,fact_count)
VALUES(%s,%s,'test-v1',%s,%s,8)""",
        (snapshot_id, body.campaign.config.as_of,
         hashlib.sha256(str(snapshot_id).encode()).hexdigest(), "a" * 64),
    )
    snapshot = {
        "snapshot_id": str(snapshot_id), "cutoff": body.campaign.config.as_of.isoformat(),
        "policy_version": "test-v1", "events_sha256": "a" * 64, "fact_count": 8,
        "asset_ids": ["asset:BTC"],
        "facts": [{"fact_id": str(uuid4()), "event_type": "listing", "severity": 0.8,
                   "available_at": "2026-01-01T01:00:00Z"}],
    }
    return worker, frozen, snapshot


@pytest.mark.asyncio
async def test_handoff_creates_one_running_campaign_and_reuses_frozen_data(database, monkeypatch):
    worker, frozen, snapshot = await ready_loop(database)
    future_fact_id = str(uuid4())
    snapshot["facts"].append({
        "fact_id": future_fact_id, "event_type": "delisting", "severity": 1,
        "available_at": "2026-01-01T09:00:00Z",
    })

    @asynccontextmanager
    async def connection():
        yield database

    fetches = []

    async def fetch(snapshot_id, **kwargs):
        fetches.append(snapshot_id)
        return snapshot

    monkeypatch.setattr(loop_campaign, "get_conn", connection)
    monkeypatch.setattr(loop_campaign, "fetch_event_snapshot", fetch)
    settings = EvolverSettings(EVENT_EVOLUTION_ENABLED=True)
    with pytest.raises(loop_authorizations.LoopAuthorizationUnavailable):
        await loop_campaign.handoff_campaign({**worker, "lease_token": uuid4()}, settings)
    assert not fetches
    campaign_id = await loop_campaign.handoff_campaign(worker, settings)
    assert await loop_campaign.handoff_campaign(worker, settings) == campaign_id
    assert len(fetches) == 1
    campaign = await campaigns.get_campaign(database, campaign_id, worker["owner_account_id"])
    assert campaign["status"] == "replaying"
    assert campaign["llm_credential_grant"] is None
    assert campaign["data_snapshot_id"] == frozen["snapshot_id"]
    assert campaign["frozen_config"]["protection_policy"] == {
        "version": "paper-protection-v1", "protective_stop_loss_pct": 0.2,
    }
    assert len(campaign["hypotheses"]) == 8
    assert future_fact_id not in json.dumps(campaign["frozen_config"])
    assert all(future_fact_id not in json.dumps(item["spec"]) for item in campaign["hypotheses"])
    assert campaign["frozen_config"]["source_simulation_feedback"]["summary"]["metric_scope"] == "loop_discovery_only"
    loop = await loops.get_loop(database, worker["loop_id"], worker["owner_account_id"])
    assert loop["status"] == "campaign_running"
    assert loop["campaign_id"] == campaign_id
