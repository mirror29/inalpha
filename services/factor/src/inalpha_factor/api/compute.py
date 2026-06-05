"""POST /compute —— 算因子时序值。"""
from __future__ import annotations

import math

from fastapi import APIRouter

from ..deps import EngineDep
from ..schemas import ComputeRequest, ComputeResponse, FactorSeriesPoint

router = APIRouter(tags=["factor"])


@router.post("/compute", response_model=ComputeResponse)
async def compute(req: ComputeRequest, engine: EngineDep) -> ComputeResponse:
    """算因子时序。warmup / NaN / inf 统一返回 null（前端友好）。"""
    df, series = await engine.compute_series(
        venue=req.venue,
        symbol=req.symbol,
        timeframe=req.timeframe,
        from_ts=req.from_ts,
        to_ts=req.to_ts,
        factor_ids=req.factor_ids,
    )
    out: dict[str, list[FactorSeriesPoint]] = {}
    for fid, s in series.items():
        points: list[FactorSeriesPoint] = []
        for ts, v in s.items():
            fv = float(v) if v is not None else None
            if fv is not None and (math.isnan(fv) or math.isinf(fv)):
                fv = None
            points.append(FactorSeriesPoint(ts=ts.to_pydatetime(), value=fv))
        out[fid] = points
    return ComputeResponse(
        venue=req.venue,
        symbol=req.symbol,
        timeframe=req.timeframe,
        bars_used=len(df),
        series=out,
    )
