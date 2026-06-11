"""POST /snapshot —— top-N 有效因子（喂 research analyst / agent timing）。"""
from __future__ import annotations

from fastapi import APIRouter

from ..deps import EngineDep
from ..schemas import FactorEffectiveness, SnapshotRequest, SnapshotResponse

router = APIRouter(tags=["factor"])


@router.post("/snapshot", response_model=SnapshotResponse)
async def snapshot(req: SnapshotRequest, engine: EngineDep) -> SnapshotResponse:
    """紧凑形状：只回按 |rank_ic| 排序的 top-N 有效因子，控制喂 LLM 的 token。"""
    result = await engine.snapshot(
        venue=req.venue,
        symbol=req.symbol,
        timeframe=req.timeframe,
        as_of=req.as_of,
        lookback_bars=req.lookback_bars,
        horizon_bars=req.horizon_bars,
        top_n=req.top_n,
    )
    return SnapshotResponse(
        venue=req.venue,
        symbol=req.symbol,
        timeframe=req.timeframe,
        as_of=result["as_of"],
        horizon_bars=req.horizon_bars,
        bars_used=result["bars_used"],
        available=result["available"],
        reason=result["reason"],
        top_factors=[FactorEffectiveness(**f) for f in result["top_factors"]],
        candidates_evaluated=result["candidates_evaluated"],
        low_confidence_count=result["low_confidence_count"],
        ic_null_benchmark=result["ic_null_benchmark"],
    )
