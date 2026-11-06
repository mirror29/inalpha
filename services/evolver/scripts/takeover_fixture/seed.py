import asyncio
import json
import sys
from pathlib import Path
from uuid import uuid4

from inalpha_shared.db import close_pool, get_conn, init_pool

from inalpha_evolver.config import EvolverSettings
from inalpha_evolver.runtime import loop_campaign

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "services/evolver"))
from tests.test_loop_handoff import ready_loop


async def main():
    import os

    await init_pool(os.environ["DATABASE_URL"], min_size=1, max_size=2)
    try:
        async with get_conn() as conn:
            worker, _, snapshot = await ready_loop(conn)
        types = [
            "listing",
            "delisting",
            "exploit",
            "chain_halt",
            "regulatory",
            "upgrade",
            "macro",
            "other",
        ]
        snapshot["facts"] = [
            {
                "fact_id": str(uuid4()),
                "event_type": kind,
                "severity": 0.8,
                "confidence": 0.9,
                "effective_at": "2026-01-01T07:00:00Z",
                "available_at": "2026-01-01T07:00:00Z",
                "assets": ["BTC"],
                "asset_ids": ["asset:BTC"],
                "extractor_version": "deterministic-event-extractor-v3",
            }
            for kind in types
        ]

        async def fetch(*args, **kwargs):
            return snapshot

        loop_campaign.fetch_event_snapshot = fetch
        campaign = await loop_campaign.handoff_campaign(
            worker, EvolverSettings(EVENT_EVOLUTION_ENABLED=True)
        )
        async with get_conn() as conn:
            await conn.execute(
                "UPDATE evolution_campaigns SET requested_by_sub=%s WHERE campaign_id=%s",
                (str(worker["owner_account_id"]), campaign),
            )
            await conn.execute(
                "UPDATE evolution_loops SET requested_by_sub=%s WHERE loop_id=%s",
                (str(worker["owner_account_id"]), worker["loop_id"]),
            )
        await asyncio.to_thread(
            Path(sys.argv[1]).write_text,
            json.dumps(
                {
                    "campaign_id": str(campaign),
                    "owner": str(worker["owner_account_id"]),
                    "loop_id": str(worker["loop_id"]),
                    "snapshot": snapshot,
                }
            ),
        )
    finally:
        await close_pool()


asyncio.run(main())
