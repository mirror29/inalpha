"""Restarting a baseline resumes persisted slots without repurchasing completed mutations."""

from contextlib import asynccontextmanager
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest

from inalpha_evolver.config import EvolverSettings
from inalpha_evolver.data.persistent_snapshot import (
    get_loop_data_snapshot,
    persist_loop_data_snapshot,
)
from inalpha_evolver.hypothesis.feedback import build_loop_simulation_feedback
from inalpha_evolver.runtime.generation import execute_generation
from inalpha_evolver.runtime.loop_baseline import execute_loop_baseline
from inalpha_evolver.storage import candidates, loop_authorizations, loop_dispatch, loops, runs

from .test_loop_storage import create_baseline, loop_database  # noqa: F401
from .test_persistent_snapshot import _dataset


@pytest.mark.asyncio
async def test_restart_skips_finished_slots_and_reuses_persisted_source(database, monkeypatch):
    args = await create_baseline(database, uuid4())
    run_id = args["e1_run_id"]
    run = await runs.transition(database, run_id, from_statuses=("queued",), to_status="running")
    await candidates.insert_slot(database, run_id, 0, "finished")
    await candidates.update_slot(database, run_id, 0, stage="completed", outcome="no_change")
    await candidates.insert_slot(database, run_id, 1, "already purchased")
    await candidates.update_slot(
        database, run_id, 1, stage="evaluation", source_code="persisted-candidate-source",
        source_hash="a" * 64, audit_snapshot={"ok": True},
    )

    @asynccontextmanager
    async def connection():
        yield database

    monkeypatch.setattr("inalpha_evolver.runtime.generation.get_conn", connection)
    monkeypatch.setattr("inalpha_evolver.runtime.slots.get_conn", connection)

    class Model:
        calls = 0

        async def mutate(self, *args):
            self.calls += 1
            return SimpleNamespace(
                unified_diff=None, llm_cost_usd=0, cache_hit_tokens=0,
                input_tokens=0, output_tokens=0,
            )

    class Evaluator:
        def __init__(self):
            self.sources = []

        async def evaluate(self, source):
            self.sources.append(source)
            return SimpleNamespace(report={}, fitness=1, data_epoch=1, overfitting_risk="low")

        async def evaluate_baseline(self):
            return {}

    model, evaluator = Model(), Evaluator()
    await execute_generation(run, mutator=model, evaluator=evaluator)
    assert model.calls == 2
    assert evaluator.sources == [run["seed_source_snapshot"], "persisted-candidate-source"]
    assert (await runs.get_run(database, run_id))["status"] == "completed"


@pytest.mark.asyncio
async def test_automatic_baseline_uses_discovery_and_restarts_without_model_calls(database, monkeypatch):
    args = await create_baseline(database, uuid4())
    await runs.transition(
        database, args["e1_run_id"], from_statuses=("queued",), to_status="queued",
        values={"config": {**args["frozen_config"], "initial_cash": 10000}},
    )
    loop = await loops.ensure_for_e1_run(database, **args)
    await loop_authorizations.register(
        database, loop_id=loop["loop_id"], owner_account_id=args["owner_account_id"],
        request_digest="a" * 64, max_cost_usd=Decimal("1"),
    )
    worker = await loop_dispatch.claim_next(database, worker_id="baseline-worker", ttl_s=60)
    await persist_loop_data_snapshot(
        database, loop_id=loop["loop_id"], owner_account_id=args["owner_account_id"],
        lease_token=worker["lease_token"], dataset=_dataset(),
    )

    @asynccontextmanager
    async def connection():
        yield database

    for module in ("loop_baseline", "executor", "generation", "slots"):
        monkeypatch.setattr(f"inalpha_evolver.runtime.{module}.get_conn", connection)

    class Model:
        calls = 0

        async def mutate(self, *args):
            self.calls += 1
            return SimpleNamespace(
                unified_diff=None, llm_cost_usd=0, cache_hit_tokens=0,
                input_tokens=0, output_tokens=0,
            )

    model = Model()
    settings = EvolverSettings()
    await execute_loop_baseline(worker, settings, mutator=model)
    assert model.calls == 4
    run = await runs.get_run(database, args["e1_run_id"])
    assert run["status"] == "completed"
    assert run["dataset_manifest"]["bar_count"] == 6
    frozen = await get_loop_data_snapshot(database, loop["loop_id"], args["owner_account_id"])
    feedback = build_loop_simulation_feedback(
        run, await candidates.list_candidates(database, run["run_id"], args["owner_account_id"]),
        frozen,
    )
    assert feedback["summary"]["metric_scope"] == "loop_discovery_only"
    assert feedback["records"][1]["metrics"]["num_bars"] == 6
    assert feedback["records"][2]["metrics"]["num_bars"] == 6
    with pytest.raises(ValueError, match="discovery"):
        build_loop_simulation_feedback(
            {**run, "dataset_manifest": _dataset().manifest.model_dump(mode="json")}, [], frozen,
        )
    assert (await loops.get_loop(database, loop["loop_id"], args["owner_account_id"]))["status"] == "baseline_ready"
    await execute_loop_baseline(worker, settings, mutator=model)
    assert model.calls == 4
