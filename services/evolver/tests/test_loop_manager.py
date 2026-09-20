"""The persistent dispatcher survives shutdown without chat or a second user action."""

import asyncio
from contextlib import asynccontextmanager

import pytest

from inalpha_evolver.config import EvolverSettings
from inalpha_evolver.runtime.loop_manager import LoopManager
from inalpha_evolver.storage import loop_dispatch, loops, runs

from .test_loop_handoff import ready_loop
from .test_loop_storage import loop_database  # noqa: F401


@pytest.mark.asyncio
async def test_restart_automatically_resumes_handoff_without_aborting_baseline(database, monkeypatch):
    worker, _, snapshot = await ready_loop(database)
    await loop_dispatch.defer(
        database, loop_id=worker["loop_id"], owner_account_id=worker["owner_account_id"],
        lease_token=worker["lease_token"], delay_s=0,
    )

    connection_lock = asyncio.Lock()

    @asynccontextmanager
    async def connection():
        async with connection_lock:
            yield database

    for module in ("loop_manager", "loop_baseline", "loop_campaign"):
        monkeypatch.setattr(f"inalpha_evolver.runtime.{module}.get_conn", connection)
    started = asyncio.Event()
    resume = False

    async def fetch(*args, **kwargs):
        started.set()
        if not resume:
            await asyncio.Event().wait()
        return snapshot

    monkeypatch.setattr("inalpha_evolver.runtime.loop_campaign.fetch_event_snapshot", fetch)
    settings = EvolverSettings(EVENT_EVOLUTION_ENABLED=True)
    first = LoopManager(settings)
    await first.start()
    try:
        await asyncio.wait_for(started.wait(), timeout=3)
        assert first.healthy
    finally:
        await first.close()
    persisted = await loops.get_loop(database, worker["loop_id"], worker["owner_account_id"])
    assert persisted["status"] == "baseline_ready"
    assert persisted["campaign_id"] is None
    assert (await runs.get_run(database, worker["e1_run_id"]))["status"] == "completed"
    resume = True
    second = LoopManager(settings)
    await second.start()
    try:
        async with asyncio.timeout(3):
            while True:
                async with connection() as conn:
                    persisted = await loops.get_loop(conn, worker["loop_id"], worker["owner_account_id"])
                if persisted["campaign_id"] is not None:
                    break
                await asyncio.sleep(0.01)
        assert persisted["status"] == "campaign_running"
    finally:
        await second.close()
