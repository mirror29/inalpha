"""``POST /backtest`` —— 跑一次回测并直接返回报告。

D-6 范围：**同步执行**。SMA cross 100 根 bar < 100ms，HTTP 同步返回够用。
D-7+ 升级为 async + jobId + polling/WS（按 [ADR-0002 §长任务 idempotency](../../../../docs/decisions/0002-cross-service-communication.md)）。
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header
from inalpha_shared.auth import User, get_current_user
from inalpha_shared.errors import UnauthorizedError, ValidationError

from ..config import PaperSettings, get_paper_settings
from ..data_client import DataClient
from ..runner import run_backtest as _run_backtest
from ..schemas import BacktestRequest, BacktestResponse
from ..strategies import list_strategies

router = APIRouter(tags=["backtest"])


@router.post("/backtest", response_model=BacktestResponse)
async def post_backtest(
    req: BacktestRequest,
    settings: Annotated[PaperSettings, Depends(get_paper_settings)],
    _user: Annotated[User, Depends(get_current_user)],
    authorization: Annotated[str | None, Header()] = None,
) -> BacktestResponse:
    """跑回测：拉数据 → 实例化策略 → 跑引擎 → 返回报告。"""
    # 业务校验
    if req.from_ts >= req.to_ts:
        raise ValidationError(
            "from_ts must be < to_ts",
            details={"from_ts": req.from_ts.isoformat(), "to_ts": req.to_ts.isoformat()},
        )

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
        return await _run_backtest(req, data_client)


@router.get("/strategies", response_model=dict)
async def get_strategies(
    _user: Annotated[User, Depends(get_current_user)],
) -> dict[str, list[str]]:
    """已注册的 strategy_id 列表。"""
    return {"strategies": list_strategies()}
