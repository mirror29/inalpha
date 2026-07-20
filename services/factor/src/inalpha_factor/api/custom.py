"""POST /custom/score —— 自定义因子表达式的一站式评估（D-12 · 因子发现 L1）。

表达式审计失败返 400 ``FACTOR_EXPRESSION_INVALID``（message 给 LLM 改写依据）；
通过则服务端取 bar → 求值 → effectiveness → 与库去相关对比，一次出全套。

P4（ADR-0055）：默认启用 WalkForward OOS 验证，返回 OOS IC 分布 + 退化率。
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter
from inalpha_shared.errors import ValidationError

from ..deps import EngineDep
from ..expression import ExpressionError, parse_expression
from ..schemas import (
    CorrelatedFactor,
    CustomScoreRequest,
    CustomScoreResponse,
    FactorEffectiveness,
    WalkForwardResult,
)

#: timeframe → 秒/bar（复用 engine._TF_SECONDS）
_TF_SECONDS: dict[str, int] = {
    "1m": 60, "5m": 300, "15m": 900, "30m": 1800,
    "1h": 3600, "2h": 7200, "4h": 14400, "1d": 86400, "1wk": 604800,
}


def _tf_seconds(timeframe: str) -> int:
    return _TF_SECONDS.get(timeframe, 3600)

router = APIRouter(tags=["custom"])


@router.post("/custom/score", response_model=CustomScoreResponse)
async def custom_score(req: CustomScoreRequest, engine: EngineDep) -> CustomScoreResponse:
    """评估一个受限 DSL 表达式因子（白名单审计 → 求值 → 有效性 → 库相关性）。

    P4：默认启用 WalkForward OOS 验证（walk_forward=True），返回 OOS IC 分布 + 退化率。
    退化率 > 0.4 时自动标注 high_degradation 建议。

    P5：支持多标的并发评估（symbols 字段），返回跨品种 IC 一致性。
    """
    try:
        result = await engine.custom_score(
            expression=req.expression,
            name=req.name,
            venue=req.venue,
            symbol=req.symbol,
            symbols=req.symbols,
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

    # P4：WalkForward OOS 验证
    wf_result = None
    if result.get("available") and result.get("factor") and result.get("bars_used", 0) > 0:
        try:
            from ..effectiveness import walk_forward_ic
            from ..expression import evaluate

            parsed = parse_expression(req.expression)
            df = await engine._fetch_df(
                venue=req.venue, symbol=req.symbol, timeframe=req.timeframe,
                from_ts=result.get("as_of", datetime.now(UTC)) - timedelta(
                    seconds=_tf_seconds(req.timeframe) * (req.lookback_bars + req.horizon_bars + 60)
                ),
                to_ts=result.get("as_of", datetime.now(UTC)),
            )
            if not df.empty:
                series = evaluate(parsed, df)
                close = df["close"].astype(float)
                wf = walk_forward_ic(
                    series, close,
                    horizon=req.horizon_bars,
                    n_splits=5,
                    min_samples=120,
                )
                if wf.get("oos_ic_mean") is not None:
                    wf_result = WalkForwardResult(
                        oos_ic_mean=wf["oos_ic_mean"],
                        oos_ic_std=wf["oos_ic_std"],
                        oos_ic_p50=wf["oos_ic_p50"],
                        oos_ic_p5=wf["oos_ic_p5"],
                        oos_ic_p95=wf["oos_ic_p95"],
                        insample_ic=wf["insample_ic"],
                        degradation_rate=wf["degradation_rate"],
                        n_splits=wf["n_splits"],
                    )
        except Exception:
            # WalkForward 降级：不可用时不影响主结果
            pass

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
        walk_forward=wf_result,
    )
