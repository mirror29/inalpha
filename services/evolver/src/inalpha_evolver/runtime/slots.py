"""演化 slot 的准备、评估与持久化。"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from inalpha_paper.evaluation_executor import WorkerExecutionError
from inalpha_paper.strategy_preparation import audit_strategy_source
from inalpha_shared.db import get_conn
from inalpha_shared.errors import ValidationError

from ..evaluator.frozen import FrozenDatasetEvaluator
from ..storage import candidates


async def persist_mutation(
    run_id: UUID,
    slot: int,
    mutation: Any,
) -> str | None:
    """校验变异；失败落终态，成功落源码并返回。"""
    if mutation.unified_diff is None:
        await reject_slot(run_id, slot, "no_change", None, usage=mutation)
        return None
    try:
        audited_source = audit_strategy_source(mutation.new_source)
    except ValidationError as exc:
        await reject_slot(run_id, slot, "ast_rejected", exc, usage=mutation)
        return None
    async with get_conn() as conn:
        if await candidates.source_exists(conn, run_id, mutation.source_hash):
            await candidates.update_slot(
                conn,
                run_id,
                slot,
                stage="completed",
                outcome="duplicate",
                unified_diff=mutation.unified_diff,
                llm_cost_usd=mutation.llm_cost_usd,
                cache_hit_tokens=mutation.cache_hit_tokens,
                input_tokens=mutation.input_tokens,
                output_tokens=mutation.output_tokens,
            )
            return None
        await candidates.update_slot(
            conn,
            run_id,
            slot,
            stage="evaluation",
            source_code=audited_source,
            source_hash=mutation.source_hash,
            unified_diff=mutation.unified_diff,
            llm_cost_usd=mutation.llm_cost_usd,
            cache_hit_tokens=mutation.cache_hit_tokens,
            input_tokens=mutation.input_tokens,
            output_tokens=mutation.output_tokens,
            audit_snapshot={"ok": True, "mode": "static_ast"},
            contract_snapshot={"ok": False, "status": "pending_worker"},
        )
    return audited_source


async def evaluate_slot(
    run_id: UUID,
    slot: int,
    source: str,
    evaluator: FrozenDatasetEvaluator,
) -> None:
    try:
        result = await evaluator.evaluate(source)
    except WorkerExecutionError as exc:
        outcome = {
            "CANDIDATE_REAUDIT_FAILED": "ast_rejected",
            "CANDIDATE_LOAD_FAILED": "contract_rejected",
            "CANDIDATE_CONTRACT_FAILED": "contract_rejected",
        }.get(exc.code, "evaluation_failed")
        await reject_slot(run_id, slot, outcome, exc)
        return
    except TimeoutError as exc:
        await reject_slot(run_id, slot, "evaluation_failed", exc)
        return
    async with get_conn() as conn:
        await candidates.update_slot(
            conn,
            run_id,
            slot,
            stage="completed",
            outcome="succeeded",
            fitness=result.fitness,
            evaluation_snapshot=result.report,
            contract_snapshot={"ok": True, "validated_in": "worker"},
            report=result.report,
            data_epoch=result.data_epoch,
            overfitting_risk=result.overfitting_risk,
        )


async def reject_slot(
    run_id: UUID,
    slot: int,
    outcome: str,
    error: BaseException | None,
    *,
    usage: Any | None = None,
) -> None:
    values: dict[str, Any] = {
        "stage": "completed",
        "outcome": outcome,
        "error_code": getattr(error, "code", None),
        "error_message": str(error)[:1000] if error else None,
    }
    if usage is not None:
        values.update(
            unified_diff=getattr(usage, "failed_diff", getattr(usage, "unified_diff", None)),
            llm_cost_usd=usage.llm_cost_usd,
            cache_hit_tokens=usage.cache_hit_tokens,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
        )
    async with get_conn() as conn:
        await candidates.update_slot(
            conn,
            run_id,
            slot,
            **values,
        )
