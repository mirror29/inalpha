"""POST /backtest/score —— 因子评估 + 自动回测闭环（P0）。

评估一个受限 DSL 表达式因子，若有效则自动构造最简策略跑 WalkForward 回测。
"""
from __future__ import annotations

from fastapi import APIRouter
from inalpha_shared.errors import ValidationError

from ..backtest_score import backtest_score
from ..deps import EngineDep, SettingsDep
from ..expression import ExpressionError
from ..schemas import (
    BacktestScoreRequest,
    BacktestScoreResponse,
    BacktestScoreResult,
    CorrelatedFactor,
    FactorEffectiveness,
)

router = APIRouter(tags=["backtest_score"])


@router.post("/backtest/score", response_model=BacktestScoreResponse)
async def post_backtest_score(
    req: BacktestScoreRequest,
    engine: EngineDep,
    settings: SettingsDep,
) -> BacktestScoreResponse:
    """评估一个自定义 DSL 表达式因子，自动构造最简策略跑 WalkForward 回测。

    返回因子有效性指标 + 回测 OOS 绩效（Sharpe / MaxDD / WinRate），
    比 IC 数值更有说服力。
    """
    try:
        result = await backtest_score(
            engine=engine,
            expression=req.expression,
            name=req.name,
            venue=req.venue,
            symbol=req.symbol,
            timeframe=req.timeframe,
            as_of=req.as_of,
            lookback_bars=req.lookback_bars,
            horizon_bars=req.horizon_bars,
            initial_cash=req.initial_cash,
            fee_rate=req.fee_rate,
            cv_splitter=req.cv_splitter,
            cv_n_folds=req.cv_n_folds,
            cv_embargo_pct=req.cv_embargo_pct,
        )
    except ExpressionError as exc:
        raise ValidationError(
            f"表达式未通过审计：{exc}",
            code="FACTOR_EXPRESSION_INVALID",
        ) from exc

    bt = result.get("backtest")
    return BacktestScoreResponse(
        venue=req.venue,
        symbol=req.symbol,
        timeframe=req.timeframe,
        as_of=result["as_of"] if "as_of" in result else None,
        horizon_bars=req.horizon_bars,
        bars_used=result.get("bars_used", 0),
        available=result["available"],
        reason=result.get("reason"),
        expression=result["expression"],
        factor=FactorEffectiveness(**result["factor"]) if result.get("factor") else None,
        ic_pvalue=result.get("ic_pvalue"),
        top_correlated=[CorrelatedFactor(**c) for c in result.get("top_correlated", [])],
        max_corr=result.get("max_corr"),
        is_likely_redundant=result.get("is_likely_redundant", False),
        backtest=BacktestScoreResult(**bt) if bt else None,
    )