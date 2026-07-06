"""E1 演化主循环 —— ``run_one_generation``。

单次演化会话就是一个 E1 闭环：种子策略 → 变异 × budget → 三道沙盒 → 评估 → 落库。
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from uuid import UUID, uuid4

from inalpha_paper.strategy_authoring.fitness import calmar_from_report

from ..evaluator import Evaluator
from ..exceptions import DiffApplyError, EvaluationError, EvaluationTimeoutError, LLMError, SandboxError
from ..mutator import Mutator
from ..mutator.mock_client import MockMutator
from ..population import Candidate, EvolutionRun
from ..sandbox import assert_safe, assert_strategy_subclass
from .hint_generator import HintGenerator
from .seed import SEED_STRATEGY_CODE

logger = logging.getLogger(__name__)


async def run_one_generation(
    run_id: UUID,
    budget: int,
    config: dict | None = None,
    mutator: Mutator | MockMutator | None = None,
    evaluator: Evaluator | None = None,
    conn: object | None = None,
) -> EvolutionRun:
    """执行单代演化循环。

    Args:
        run_id: 演化会话 ID。
        budget: 本代预算（候选数量）。
        config: 演化配置（含 universe, period, timeframe, initial_cash 等）。
        mutator: 变异算子（默认 Mutator；可传 MockMutator 用于测试）。
        evaluator: 评估器（默认 Evaluator）。
        conn: DB 连接（可选，暂未实现）。

    Returns:
        更新后的 ``EvolutionRun`` 对象（含统计计数器）。
    """
    mutator = mutator or Mutator()
    evaluator = evaluator or Evaluator()
    hints = HintGenerator()

    # 配置
    cfg = config or {}
    universe = cfg.get("universe", ["BTCUSDT"])
    period_from = cfg.get("period_from", "2025-01-01")
    period_to = cfg.get("period_to", "2025-12-31")
    timeframe = cfg.get("timeframe", "1h")
    initial_cash = cfg.get("initial_cash", 10000.0)

    run = EvolutionRun(
        run_id=run_id,
        seed_strategy_id="sma_cross_v1",
        budget=budget,
        config=cfg,
        started_at=datetime.now(timezone.utc),
    )

    # 先评估种子策略（baseline）
    logger.info("评估种子策略 (baseline)...")
    try:
        seed_eval = await evaluator.evaluate(
            source_code=SEED_STRATEGY_CODE,
            universe=universe,
            period_from=period_from,
            period_to=period_to,
            timeframe=timeframe,
            initial_cash=initial_cash,
        )
    except (EvaluationError, EvaluationTimeoutError) as exc:
        logger.error("种子策略评估失败：%s", exc)
        run.status = "failed"
        run.finished_at = datetime.now(timezone.utc)
        return run

    seed_report = seed_eval.report
    logger.info("种子策略 fitness=%.4f, sharpe=%s", seed_eval.fitness,
                seed_report.get("sharpe"))

    for i in range(budget):
        hint = hints.next()
        logger.info("候选 %d/%d, hint=%s", i + 1, budget, hint)

        # 1. 变异
        try:
            mut_res = await mutator.mutate(
                current_source=SEED_STRATEGY_CODE,
                report=seed_report,
                hint=hint,
            )
        except LLMError as exc:
            logger.warning("LLM 变异失败（候选 %d）：%s", i + 1, exc)
            run.failed_eval += 1
            continue
        except DiffApplyError as exc:
            logger.warning("diff 应用失败（候选 %d）：%s", i + 1, exc)
            run.failed_eval += 1
            continue

        run.llm_cost_usd += mut_res.llm_cost_usd

        # 空 diff → 跳过
        if mut_res.unified_diff is None:
            logger.info("候选 %d：LLM 认为无需改动，跳过", i + 1)
            run.rejected_ast += 1
            continue

        # 2. AST 审计（第 1 道沙盒）
        try:
            assert_safe(mut_res.new_source)
        except SandboxError as exc:
            logger.warning("AST 审计拒绝（候选 %d）：%s", i + 1, exc)
            run.rejected_ast += 1
            continue

        # 3. 契约校验（第 2 道沙盒）
        try:
            assert_strategy_subclass(mut_res.new_source)
        except SandboxError as exc:
            logger.warning("契约校验拒绝（候选 %d）：%s", i + 1, exc)
            run.rejected_contract += 1
            continue

        # 4. 回测评估（第 3 道沙盒 = 子进程隔离）
        try:
            eval_res = await evaluator.evaluate(
                source_code=mut_res.new_source,
                universe=universe,
                period_from=period_from,
                period_to=period_to,
                timeframe=timeframe,
                initial_cash=initial_cash,
            )
        except (EvaluationError, EvaluationTimeoutError) as exc:
            logger.warning("回测评估失败（候选 %d）：%s", i + 1, exc)
            run.failed_eval += 1
            continue

        # 5. 构建候选记录
        candidate = Candidate(
            candidate_id=uuid4(),
            run_id=run_id,
            generation=1,
            parent_id=None,
            source_code=mut_res.new_source,
            source_hash=mut_res.source_hash,
            unified_diff=mut_res.unified_diff,
            mutation_hint=hint,
            llm_cost_usd=mut_res.llm_cost_usd,
            cache_hit_tokens=mut_res.cache_hit_tokens,
            fitness=eval_res.fitness,
            report=eval_res.report,
            overfitting_risk=eval_res.overfitting_risk,
            data_epoch=eval_res.data_epoch,
            created_at=datetime.now(timezone.utc),
        )

        run.candidates_count += 1
        logger.info("候选 %d 评估完成：fitness=%.4f", i + 1, eval_res.fitness)

    # 更新运行状态
    run.status = "completed"
    run.finished_at = datetime.now(timezone.utc)

    logger.info(
        "演化轮次完成：total=%d, ast_rejected=%d, contract_rejected=%d, "
        "eval_failed=%d, llm_cost=%.4f",
        run.candidates_count,
        run.rejected_ast,
        run.rejected_contract,
        run.failed_eval,
        run.llm_cost_usd,
    )

    return run