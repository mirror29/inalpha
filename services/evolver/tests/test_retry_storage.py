"""Real database evidence for attempt lineage, uniqueness and owner-scoped retry gates."""
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from inalpha_shared.errors import ConflictError, NotFoundError
from psycopg.errors import UniqueViolation

from inalpha_evolver.api.retry import retry_parent
from inalpha_evolver.storage import loops, runs

from .llm_snapshot_fixtures import llm_snapshot
from .test_loop_storage import create_baseline, loop_database  # noqa: F401


async def insert_retry(conn, parent, key):
    return await runs.insert_run(
        conn, owner_account_id=parent["owner_account_id"], requested_by_sub=str(parent["owner_account_id"]),
        idempotency_key=key, request_hash="b" * 64, seed_strategy_id=parent["seed_strategy_id"],
        seed_source=parent["seed_source_snapshot"], seed_hash=parent["seed_source_hash"],
        budget=parent["budget"], config=parent["config"], llm_snapshot=llm_snapshot(),
        llm_credential_grant="test-grant-" + "x" * 120, queued_at=datetime.now(UTC), retry_parent=parent,
    )


@pytest.mark.asyncio
async def test_retry_lineage_and_duplicate_operation_reuse(database):
    args = await create_baseline(database, uuid4())
    parent = await runs.transition(database, args["e1_run_id"], from_statuses=("queued",), to_status="failed")
    owned = await retry_parent(database, parent["run_id"], parent["owner_account_id"])
    key = str(uuid4())
    retry, created = await insert_retry(database, owned, key)
    repeat, repeated = await insert_retry(database, owned, key)
    assert created and not repeated
    assert retry["run_id"] == repeat["run_id"]
    assert retry["experiment_id"] == parent["experiment_id"] == parent["run_id"]
    assert retry["retry_of_run_id"] == parent["run_id"]
    assert retry["attempt_number"] == 2
    with pytest.raises(UniqueViolation):
        async with database.transaction():
            await insert_retry(database, owned, str(uuid4()))
    await runs.transition(database, retry["run_id"], from_statuses=("queued",), to_status="failed")
    third, _ = await insert_retry(database, retry, str(uuid4()))
    assert third["attempt_number"] == 3
    assert third["experiment_id"] == parent["experiment_id"]


@pytest.mark.asyncio
async def test_retry_rejects_foreign_active_and_automatic_baseline(database):
    args = await create_baseline(database, uuid4())
    with pytest.raises(NotFoundError):
        await retry_parent(database, args["e1_run_id"], uuid4())
    with pytest.raises(ConflictError):
        await retry_parent(database, args["e1_run_id"], args["owner_account_id"])
    await loops.ensure_for_e1_run(database, **args)
    await runs.transition(database, args["e1_run_id"], from_statuses=("queued",), to_status="failed")
    with pytest.raises(ConflictError) as error:
        await retry_parent(database, args["e1_run_id"], args["owner_account_id"])
    assert error.value.code == "EVOLUTION_RETRY_NOT_ALLOWED"
