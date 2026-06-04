"""POST /score —— 因子有效性（前瞻收益分位 + Rank IC）。"""
from __future__ import annotations

from fastapi import APIRouter

from ..deps import EngineDep
from ..schemas import FactorEffectiveness, ScoreRequest, ScoreResponse

router = APIRouter(tags=["factor"])


@router.post("/score", response_model=ScoreResponse)
async def score(req: ScoreRequest, engine: EngineDep) -> ScoreResponse:
    """对一组因子算有效性，给 agent 做"有效因子择时"的数据背书。"""
    result = await engine.score(
        venue=req.venue,
        symbol=req.symbol,
        timeframe=req.timeframe,
        as_of=req.as_of,
        lookback_bars=req.lookback_bars,
        horizon_bars=req.horizon_bars,
        quantiles=req.quantiles,
        factor_ids=req.factor_ids,
    )
    return ScoreResponse(
        venue=req.venue,
        symbol=req.symbol,
        timeframe=req.timeframe,
        as_of=result["as_of"],
        horizon_bars=req.horizon_bars,
        bars_used=result["bars_used"],
        factors=[FactorEffectiveness(**f) for f in result["factors"]],
    )
