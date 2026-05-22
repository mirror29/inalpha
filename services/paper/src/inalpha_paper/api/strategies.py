"""``POST /strategies/compose`` —— D-8c · 把 StrategyHint 路由到 strategy_id + params。

设计：

- 同步、无副作用 —— 纯函数路由，不查 DB / 不调下游
- 让 orchestration 层在跑 backtest 之前先调一次 compose，再用结果调 ``POST /backtest``
- compose 拒绝（``family='none'``）时返回 200 + ``strategy_id: null`` + ``rejected_reason``，
  orchestration 层应据此提示用户"研究结果不足以驱动策略"
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from inalpha_shared.auth import User, get_current_user

from ..strategies.compose import ComposeRequest, ComposeResult, compose_strategy

router = APIRouter(tags=["strategies"])


@router.post("/strategies/compose", response_model=ComposeResult)
async def post_compose(
    req: ComposeRequest,
    _user: Annotated[User, Depends(get_current_user)],
) -> ComposeResult:
    """把 research 服务给的 ``StrategyHint`` 翻译成可执行的 ``strategy_id + params``。"""
    return compose_strategy(req.hint, req.factors, timeframe=req.timeframe)
