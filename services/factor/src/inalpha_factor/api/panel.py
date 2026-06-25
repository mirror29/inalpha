"""POST /panel/score —— 横截面因子评估。

一篮子标的的横截面 rank-IC + 最新横截面排名,给"自动选标的"（如聚宽式按因子轮动）
与"横截面因子有效性"做数据背书。与单标的 /score 正交（时序 vs 横截面）。
"""
from __future__ import annotations

from fastapi import APIRouter

from ..deps import EngineDep
from ..schemas import (
    PanelFactorResult,
    PanelScoreRequest,
    PanelScoreResponse,
)

router = APIRouter(tags=["factor"])


@router.post("/panel/score", response_model=PanelScoreResponse)
async def panel_score(req: PanelScoreRequest, engine: EngineDep) -> PanelScoreResponse:
    """在 universe 上算每个因子的横截面 rank-IC + 最近一期排名（选标的用）。"""
    result = await engine.panel_score(
        symbols=req.symbols,
        venue=req.venue,
        timeframe=req.timeframe,
        as_of=req.as_of,
        lookback_bars=req.lookback_bars,
        horizon_bars=req.horizon_bars,
        factor_ids=req.factor_ids,
        min_symbols=req.min_symbols,
    )
    return PanelScoreResponse(
        venue=req.venue,
        timeframe=req.timeframe,
        as_of=result["as_of"],
        horizon_bars=req.horizon_bars,
        symbols=result["symbols"],
        bars_used=result["bars_used"],
        latest_bar_ts=result["latest_bar_ts"],
        is_pit=result["is_pit"],
        universe_note=result["universe_note"],
        factors=[PanelFactorResult(**f) for f in result["factors"]],
        ic_null_benchmark=result["ic_null_benchmark"],
        reason=result["reason"],
        unknown_factor_ids=result["unknown_factor_ids"],
    )
