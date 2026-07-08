"""Evolver API 路由 —— 3 个端点。

- ``POST /runs`` — 启动演化运行
- ``GET /runs/{run_id}`` — 查询运行状态 + 候选列表
- ``GET /candidates/{candidate_id}`` — 查询单个候选详情
"""
from __future__ import annotations

import logging
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import JSONResponse

from ..governor import run_one_generation
from ..mutator import Mutator
from .schemas import CandidateResponse, RunStatusResponse, StartRunRequest

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["evolution"])

# 内存存储（E1 过渡方案：E2 改为 DB 存储）
_runs: dict[UUID, RunStatusResponse] = {}
_candidates: dict[UUID, CandidateResponse] = {}


@router.get("/runs", response_model=list[RunStatusResponse])
async def list_runs() -> list[RunStatusResponse]:
    """列出所有演化运行（按 started_at 降序）。

    E1 内存存储，返回全部。E2 改为 DB 分页查询。
    """
    return sorted(
        _runs.values(),
        key=lambda r: r.started_at,
        reverse=True,
    )


@router.post("/runs", response_model=RunStatusResponse, status_code=status.HTTP_202_ACCEPTED)
async def start_run(request: StartRunRequest) -> RunStatusResponse:
    """启动一次演化运行。

    E1 用 MockMutator（不接真实 LLM API），返回手写 diff 验证闭环。
    E2 切换为真实 ``Mutator``（接 ``_shared/llm`` 的 Anthropic client）。

    Body:
        - seed_strategy_id: 种子策略 ID（默认 "sma_cross_v1"）
        - budget: 变异预算数（默认 4）
        - config: 演化配置（universe, period, timeframe, initial_cash）

    Returns 202 Accepted + run_id 用于后续轮询。
    """
    config_dict = request.config.model_dump() if request.config else {}
    run_id = uuid4()

    # 用真实 Mutator（走 GLM-5.2 / DeepSeek API）
    mutator = Mutator()

    run = await run_one_generation(
        run_id=run_id,
        budget=request.budget,
        config=config_dict,
        mutator=mutator,
        evaluator=None,
        conn=None,
    )

    resp = RunStatusResponse(
        run_id=run.run_id,
        seed_strategy_id=request.seed_strategy_id,
        budget=request.budget,
        config=config_dict,
        status=run.status,
        llm_cost_usd=run.llm_cost_usd,
        candidates_count=run.candidates_count,
        rejected_ast=run.rejected_ast,
        rejected_contract=run.rejected_contract,
        failed_eval=run.failed_eval,
    )
    _runs[run.run_id] = resp
    return resp


@router.get("/runs/{run_id}", response_model=RunStatusResponse)
async def get_run(run_id: UUID) -> RunStatusResponse:
    """查询演化运行状态。

    Returns 200 + 运行状态 + 候选列表（按 fitness 降序）。
    """
    if run_id not in _runs:
        raise HTTPException(status_code=404, detail="运行不存在")
    return _runs[run_id]


@router.get("/candidates/{candidate_id}", response_model=CandidateResponse)
async def get_candidate(candidate_id: UUID) -> CandidateResponse:
    """查询单个候选策略详情。

    Returns 200 + 候选信息（含源码 + 报告 + fitness）。
    """
    if candidate_id not in _candidates:
        raise HTTPException(status_code=404, detail="候选不存在")
    return _candidates[candidate_id]