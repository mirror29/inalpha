"""单代演化 run 的顺序调度。"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from inalpha_shared.db import get_conn

from ..evaluator.frozen import FrozenDatasetEvaluator
from ..exceptions import DiffApplyError, LLMError
from ..governor.hint_generator import HintGenerator
from ..storage import candidates, runs
from .slots import evaluate_slot, persist_mutation, reject_slot


async def execute_generation(
    run: dict[str, Any],
    *,
    mutator: Any,
    evaluator: FrozenDatasetEvaluator,
) -> None:
    source = run["seed_source_snapshot"]
    seed_result, baseline_snapshot = await _evaluate_seed_and_baseline(evaluator, source)
    async with get_conn() as conn:
        current = await runs.transition(
            conn,
            run["run_id"],
            from_statuses=("running",),
            to_status="running",
            values={
                "seed_report_snapshot": seed_result.report,
                "baseline_snapshot": baseline_snapshot,
                "active_stage": "mutating",
            },
        )
    if current is None:
        raise asyncio.CancelledError
    hints = HintGenerator()
    for slot in range(run["budget"]):
        await _checkpoint(run["run_id"])
        hint = hints.next()
        async with get_conn() as conn:
            persisted = await candidates.insert_slot(conn, run["run_id"], slot, hint)
        if persisted["outcome"] != "pending":
            continue
        if persisted["stage"] == "evaluation" and persisted["source_code"]:
            await evaluate_slot(run["run_id"], slot, persisted["source_code"], evaluator)
            continue
        try:
            mutation = await mutator.mutate(source, seed_result.report, hint)
        except LLMError as exc:
            await reject_slot(run["run_id"], slot, "mutation_failed", exc)
            continue
        except DiffApplyError as exc:
            await reject_slot(run["run_id"], slot, "diff_failed", exc, usage=exc)
            continue
        candidate_source = await persist_mutation(run["run_id"], slot, mutation)
        if candidate_source is not None:
            await evaluate_slot(run["run_id"], slot, candidate_source, evaluator)
    async with get_conn() as conn:
        summary = await candidates.summarize(conn, run["run_id"])
        completed = await runs.transition(
            conn,
            run["run_id"],
            from_statuses=("running",),
            to_status="completed",
            values={
                "active_stage": "completed",
                "finished_at": datetime.now(UTC),
                "llm_cost_usd": summary["llm_cost_usd"],
            },
        )
    if completed is None:
        raise asyncio.CancelledError


async def _evaluate_seed_and_baseline(
    evaluator: FrozenDatasetEvaluator,
    source: str,
) -> tuple[Any, dict[str, Any]]:
    """任一评估失败时取消并等待 sibling，避免越过 run 并发预算。"""
    tasks = [
        asyncio.create_task(evaluator.evaluate(source)),
        asyncio.create_task(evaluator.evaluate_baseline()),
    ]
    try:
        seed_result, baseline = await asyncio.gather(*tasks)
        return seed_result, baseline
    except BaseException:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise


async def _checkpoint(run_id: UUID) -> None:
    async with get_conn() as conn:
        current = await runs.get_run(conn, run_id)
    if current is None or current["status"] != "running":
        raise asyncio.CancelledError
