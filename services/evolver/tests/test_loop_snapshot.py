"""A baseline and its campaign share one immutable, fenced market dataset."""

from uuid import uuid4

import pytest

from inalpha_evolver.data import persistent_snapshot as snapshots
from inalpha_evolver.storage import loop_dispatch, loops

from .test_loop_storage import create_baseline, create_campaign, loop_database
from .test_persistent_snapshot import _dataset

__all__ = ["loop_database"]


@pytest.mark.asyncio
async def test_loop_snapshot_survives_handoff_without_refetch_or_replacement(database):
    args = await create_baseline(database, uuid4())
    loop = await loops.ensure_for_e1_run(database, **args)
    worker = await loop_dispatch.claim_next(database, worker_id="snapshot-worker")
    scope = {
        "loop_id": loop["loop_id"],
        "owner_account_id": args["owner_account_id"],
        "lease_token": worker["lease_token"],
    }
    dataset = _dataset()
    first = await snapshots.persist_loop_data_snapshot(database, **scope, dataset=dataset)
    assert snapshots.decode_frozen_dataset(first) == dataset
    assert snapshots.snapshot_splits(first) == (6, 8)
    changed = type(dataset)(bars=dataset.bars[:-1], manifest=dataset.manifest)
    assert (await snapshots.persist_loop_data_snapshot(
        database, **scope, dataset=changed
    ))["snapshot_id"] == first["snapshot_id"]
    with pytest.raises(RuntimeError, match="lease"):
        await snapshots.persist_loop_data_snapshot(
            database, **{**scope, "lease_token": uuid4()}, dataset=dataset
        )
    assert await snapshots.get_loop_data_snapshot(database, loop["loop_id"], uuid4()) is None
    campaign_id = await create_campaign(database, args)
    await database.execute(
        "UPDATE strategy_evo_runs SET status='completed' WHERE run_id=%s", (args["e1_run_id"],)
    )
    assert await loop_dispatch.complete_step(
        database, **scope, step_key="baseline", output_id=args["e1_run_id"]
    )
    await loops.ensure_for_campaign(
        database, **args, campaign_id=campaign_id, lease_token=scope["lease_token"]
    )
    inherited = await snapshots.get_campaign_data_snapshot(database, campaign_id)
    assert inherited["snapshot_id"] == first["snapshot_id"]
    assert snapshots.decode_frozen_dataset(inherited) == dataset
    campaign_lease = uuid4()
    await database.execute(
        """UPDATE evolution_campaigns SET lease_token=%s,
lease_expires_at=clock_timestamp()+INTERVAL '60 seconds' WHERE campaign_id=%s""",
        (campaign_lease, campaign_id),
    )
    retry = await snapshots.persist_campaign_data_snapshot(
        database, campaign_id=campaign_id, owner_account_id=args["owner_account_id"],
        lease_token=campaign_lease, dataset=changed,
    )
    assert retry["snapshot_id"] == first["snapshot_id"]
