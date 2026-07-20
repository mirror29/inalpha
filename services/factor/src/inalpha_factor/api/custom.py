"""POST /custom/score —— 自定义因子表达式的一站式评估（D-12 · 因子发现 L1）。

表达式审计失败返 400 ``FACTOR_EXPRESSION_INVALID``（message 给 LLM 改写依据）；
通过则服务端取 bar → 求值 → effectiveness → 与库去相关对比，一次出全套。
"""
from __future__ import annotations

from fastapi import APIRouter
from inalpha_shared.errors import ValidationError

from ..deps import EngineDep
from ..expression import ExpressionError
from ..schemas import (
    CorrelatedFactor,
    CustomScoreRequest,
    CustomScoreResponse,
    FactorEffectiveness,
)

router = APIRouter(tags=["custom"])


@router.post("/custom/score", response_model=CustomScoreResponse)
async def custom_score(req: CustomScoreRequest, engine: EngineDep) -> CustomScoreResponse:
    """评估一个受限 DSL 表达式因子（白名单审计 → 求值 → 有效性 → 库相关性）。"""
    try:
        result = await engine.custom_score(
            expression=req.expression,
            name=req.name,
            venue=req.venue,
            symbol=req.symbol,
            timeframe=req.timeframe,
            as_of=req.as_of,
            lookback_bars=req.lookback_bars,
            horizon_bars=req.horizon_bars,
            quantiles=req.quantiles,
        )
    except ExpressionError as exc:
        raise ValidationError(
            f"表达式未通过审计：{exc}",
            code="FACTOR_EXPRESSION_INVALID",
        ) from exc

    return CustomScoreResponse(
        venue=req.venue,
        symbol=req.symbol,
        timeframe=req.timeframe,
        as_of=result["as_of"],
        horizon_bars=req.horizon_bars,
        bars_used=result["bars_used"],
        available=result["available"],
        reason=result["reason"],
        expression=result["expression"],
        factor=FactorEffectiveness(**result["factor"]) if result["factor"] else None,
        ic_pvalue=result["ic_pvalue"],
        top_correlated=[CorrelatedFactor(**c) for c in result["top_correlated"]],
        max_corr=result["max_corr"],
        is_likely_redundant=result["is_likely_redundant"],
    )
