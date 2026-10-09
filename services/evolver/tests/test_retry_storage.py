"""Real database evidence for attempt lineage, uniqueness and owner-scoped retry gates."""
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from inalpha_shared.errors import ConflictError, NotFoundError
from psycopg.errors import UniqueViolation

from inalpha_evolver.api.retry import retry_parent
from inalpha_evolver.runtime.repair import prepare_repair
from inalpha_evolver.storage import candidates, loops, runs

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


@pytest.mark.asyncio
async def test_mutation_repair_link_survives_database_reload_and_is_owner_scoped(database):
    args = await create_baseline(database, uuid4())
    run = {"run_id": args["e1_run_id"], "owner_account_id": args["owner_account_id"]}
    first = await candidates.insert_slot(database, run["run_id"], 0, "initial")
    await candidates.update_slot(
        database, run["run_id"], 0, stage="completed", outcome="diff_failed",
        error_code="MUTATION_CONTEXT_MISMATCH", unified_diff="failed artifact",
        llm_cost_usd=0.01, input_tokens=100, output_tokens=20,
    )
    second = await candidates.insert_slot(database, run["run_id"], 1, "ordinary")
    hint = await prepare_repair(database, run, second)
    reloaded = await candidates.list_candidates(database, run["run_id"], run["owner_account_id"])
    assert reloaded[1]["parent_id"] == first["candidate_id"]
    assert await prepare_repair(database, run, reloaded[1]) == hint
    await candidates.update_slot(
        database, run["run_id"], 1, stage="completed", outcome="diff_failed",
        error_code="MUTATION_DIFF_INVALID", llm_cost_usd=0.02,
    )
    third = await candidates.insert_slot(database, run["run_id"], 2, "ordinary")
    assert await prepare_repair(database, run, third) == "ordinary"
    rows = await candidates.list_candidates(database, run["run_id"], run["owner_account_id"])
    assert sum(row["parent_id"] is not None for row in rows) == 1
    assert await candidates.list_candidates(database, run["run_id"], uuid4()) == []
    assert rows[0]["unified_diff"] == "failed artifact"
    assert float((await candidates.summarize(database, run["run_id"]))["llm_cost_usd"]) == pytest.approx(0.03)


@pytest.mark.asyncio
async def test_model_claim_and_unknown_cost_survive_reload_without_replay(database):
    args = await create_baseline(database, uuid4())
    run_id = args["e1_run_id"]
    slot = await candidates.insert_slot(database, run_id, 0, "initial")
    assert slot["usage_status"] == "not_called"
    assert await candidates.claim_model_call(database, run_id, 0)
    assert not await candidates.claim_model_call(database, run_id, 0)
    rows = await candidates.list_candidates(database, run_id, args["owner_account_id"])
    assert rows[0]["usage_status"] == "unknown"
    assert rows[0]["llm_cost_usd"] is None
    summary = await candidates.summarize(database, run_id)
    assert summary["unknown_usage_count"] == 1
    assert summary["known_cost_usd"] == 0
    await candidates.update_slot(
        database, run_id, 0, usage_status="known", llm_cost_usd=0.01,
        input_tokens=100, output_tokens=20, stage="completed", outcome="diff_failed",
    )
    summary = await candidates.summarize(database, run_id)
    assert summary["unknown_usage_count"] == 0
    assert float(summary["known_cost_usd"]) == pytest.approx(0.01)
