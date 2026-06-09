"""``POST /backtest`` —— 跑一次回测并直接返回报告。

D-6 范围：**同步执行**。SMA cross 100 根 bar < 100ms，HTTP 同步返回够用。
D-7+ 升级为 async + jobId + polling/WS（按 [ADR-0002 §长任务 idempotency](../../../../docs/decisions/0002-cross-service-communication.md)）。
D-8c 起：每次回测落 ``backtest_runs`` 表，含 research_id / params_hash 血缘。
"""
from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query
from inalpha_shared.auth import User, get_current_user
from inalpha_shared.db import DBConn
from inalpha_shared.errors import UnauthorizedError, ValidationError

from ..config import PaperSettings, get_paper_settings
from ..data_client import DataClient
from ..runner import run_backtest as _run_backtest
from ..schemas import (
    BacktestRequest,
    BacktestResponse,
    BacktestRunSummary,
    BacktestTradeRecord,
)
from ..storage import backtest_runs as backtest_runs_store
from ..storage import backtest_trades as backtest_trades_store
from ..strategies import list_strategies

router = APIRouter(tags=["backtest"])


@router.post("/backtest", response_model=BacktestResponse)
async def post_backtest(
    req: BacktestRequest,
    db: DBConn,
    settings: Annotated[PaperSettings, Depends(get_paper_settings)],
    _user: Annotated[User, Depends(get_current_user)],
    authorization: Annotated[str | None, Header()] = None,
) -> BacktestResponse:
    """跑回测：拉数据 → 实例化策略 → 跑引擎 → 落库 → 返回报告。

    D-9 起：``strategy_id`` 与 ``candidate_id`` 二选一（Pydantic ``model_validator``
    保证两者必有其一）。candidate 路径下 strategy_id 校验跳过——LLM 候选不在内置
    注册表里。
    """
    # 业务校验
    if req.from_ts >= req.to_ts:
        raise ValidationError(
            "from_ts must be < to_ts",
            details={"from_ts": req.from_ts.isoformat(), "to_ts": req.to_ts.isoformat()},
        )

    # 内置策略路径才查注册表；candidate 路径在 runner 里读 DB 自带校验
    if req.strategy_id is not None:
        available = list_strategies()
        if req.strategy_id not in available:
            raise ValidationError(
                f"unknown strategy_id {req.strategy_id!r}",
                details={"available": available},
            )

    # 取 forward 用的 JWT（用户 token），用来调 data-service
    if not authorization or not authorization.startswith("Bearer "):
        # 理论上 get_current_user 已经查过，但 mypy 不知道
        raise UnauthorizedError("missing Authorization header")
    user_token = authorization.removeprefix("Bearer ").strip()

    async with DataClient(settings.data_service_url, user_token) as data_client:
        return await _run_backtest(req, data_client, conn=db)


@router.get("/strategies", response_model=dict)
async def get_strategies(
    _user: Annotated[User, Depends(get_current_user)],
) -> dict[str, list[str]]:
    """已注册的 strategy_id 列表。"""
    return {"strategies": list_strategies()}


@router.get("/backtest_runs", response_model=list[BacktestRunSummary])
async def get_backtest_runs(
    db: DBConn,
    _user: Annotated[User, Depends(get_current_user)],
    research_id: Annotated[UUID | None, Query()] = None,
    strategy_code: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> list[BacktestRunSummary]:
    """查历史回测。

    至少给 ``research_id`` 或 ``strategy_code`` 之一（同时给 → 优先用 research_id）。
    用途：agent 决策"是否复用上一次回测"，避免重复计算同 params 的 backtest。
    """
    if research_id is None and strategy_code is None:
        raise ValidationError(
            "must provide research_id or strategy_code",
            code="MISSING_FILTER",
        )

    if research_id is not None:
        rows = await backtest_runs_store.list_by_research(db, research_id, limit=limit)
    else:
        assert strategy_code is not None  # narrow for mypy
        rows = await backtest_runs_store.list_by_strategy(db, strategy_code, limit=limit)

    return [
        BacktestRunSummary(
            run_id=r["id"],
            strategy_code=r["strategy_code"] or "unknown",
            params_hash=r["params_hash"],
            research_id=r["research_id"],
            config=r["config"] or {},
            metrics=r["metrics"] or {},
            strategy_hint=r["strategy_hint"],
            status=r["status"],
            created_at=r["created_at"],
        )
        for r in rows
    ]


@router.get(
    "/backtest_runs/{run_id}/trades",
    response_model=list[BacktestTradeRecord],
)
async def get_backtest_run_trades(
    run_id: UUID,
    db: DBConn,
    _user: Annotated[User, Depends(get_current_user)],
    limit: Annotated[int, Query(ge=1, le=2000)] = 500,
) -> list[BacktestTradeRecord]:
    """一次回测的**逐笔成交**（含每笔实现盈亏），按成交先后（seq）排序。

    用途：策略详情页 ``/lab/[id]`` 展示该候选最近一次回测的逐笔买卖 + 盈亏复盘。
    run 不存在 / 无成交时返回空数组（不报错）。
    """
    rows = await backtest_trades_store.list_by_run(db, run_id, limit=limit)
    return [
        BacktestTradeRecord(
            seq=r["seq"],
            bar_ts=r["bar_ts"],
            bar_close=r["bar_close"],
            side=r["side"],
            quantity=r["quantity"],
            order_type=r["order_type"],
            fill_price=r["fill_price"],
            fee=r["fee"],
            realized_pnl=r["realized_pnl"],
            intent=r["intent"],
            tag=r["tag"],
        )
        for r in rows
    ]
