"""参数敏感性检查（D-12）—— promote 前对最终参数做邻域扰动回测。

动机：单点回测的 fitness 说明不了"这套参数是不是恰好踩中历史"。对每个数值参数
做 one-at-a-time ±pct 扰动各跑一次，邻域 fitness 断崖式低于 base = 过拟合信号
（参数面是尖峰不是高原）。

设计边界：

- **bars 只拉一次**，base + 邻域全部经 ``runner._run_engine`` 走 ProcessPool 并发
- **邻域 run 不落 backtest_runs**（避免十几条扰动 run 污染回测历史）；只把摘要
  merge 进 ``candidate.metrics.sensitivity`` 供 promote 门槛读
- one-at-a-time（2P 个组合）而非全笛卡尔：P 个参数全网格是 3^P，16 个组合的预算
  下覆盖不了；OAT 已足够暴露"单参数断崖"
- 非法组合（如 fast >= slow 被策略构造拒绝）记 error 不阻断，计入 n_failed
"""
from __future__ import annotations

import asyncio
import statistics
from typing import Any

from inalpha_shared.errors import NotFoundError, ValidationError
from psycopg import AsyncConnection

from .data_client import DataClient
from .engine.metrics import periods_per_year
from .kernel.identifiers import InstrumentId
from .runner import _bar_from_dict, _fitness_from_report, _run_engine
from .schemas import (
    SensitivityNeighbor,
    SensitivityRequest,
    SensitivityResponse,
    SensitivityStats,
)
from .storage import strategy_candidates as candidates_store
from .strategy_authoring import audit_strategy_code

#: 邻域组合数硬上限（服务端兜底，schema 也限）：16 组 × 单次引擎 run 的 CPU 预算。
MAX_COMBOS = 16

#: 这些参数只缩放仓位/费用，不改变信号逻辑——扰动它们浪费组合预算。
_SIZING_PARAM_NAMES = frozenset({"trade_size", "position_pct"})


def build_neighborhood(
    params: dict[str, Any],
    *,
    pct: float = 0.2,
    max_combos: int = MAX_COMBOS,
) -> list[dict[str, Any]]:
    """对数值参数做 one-at-a-time ±pct 扰动，生成邻域参数组合（纯函数）。

    - int 参数取整去重；±pct 取整后等于原值时退化为 ±1（小整数如 fast=3 也有邻域）
    - bool 不算数值（bool 是 int 子类，必须显式排除）
    - sizing 参数（trade_size / position_pct）跳过——只缩放仓位，不改信号逻辑
    - 与 base 相同的组合剔除；按参数名排序保证输出确定性
    """
    combos: list[dict[str, Any]] = []
    seen: set[str] = {_combo_key(params)}
    for name in sorted(params):
        value = params[name]
        if name in _SIZING_PARAM_NAMES:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        for direction in (-1, 1):
            if isinstance(value, int):
                perturbed: int | float = round(value * (1 + direction * pct))
                if perturbed == value:
                    perturbed = value + direction
            else:
                perturbed = round(value * (1 + direction * pct), 10)
            variant = {**params, name: perturbed}
            key = _combo_key(variant)
            if key in seen:
                continue
            seen.add(key)
            combos.append(variant)
            if len(combos) >= max_combos:
                return combos
    return combos


def _combo_key(params: dict[str, Any]) -> str:
    return repr(sorted(params.items()))


def summarize_neighbors(
    base_fitness: float,
    neighbors: list[SensitivityNeighbor],
) -> tuple[SensitivityStats, str]:
    """邻域结果 → (stats, verdict)（纯函数）。

    verdict:
    - ``insufficient``：成功邻域 < 4 组，或 base fitness ≤ 0（base 本身不及格，
      敏感性无意义——先把策略改到 fitness > 0 再谈稳健）
    - ``cliff``：邻域最差 < 0.5 × base —— 单参数小扰动就掉一半 = 参数尖峰
    - ``robust``：其余
    """
    ok_values = [n.fitness for n in neighbors if n.fitness is not None]
    n_failed = len(neighbors) - len(ok_values)
    stats = SensitivityStats(
        mean=statistics.fmean(ok_values) if ok_values else None,
        std=statistics.stdev(ok_values) if len(ok_values) >= 2 else None,
        worst=min(ok_values) if ok_values else None,
        n_ok=len(ok_values),
        n_failed=n_failed,
    )
    if base_fitness <= 0 or len(ok_values) < 4:
        return stats, "insufficient"
    if stats.worst is not None and stats.worst < 0.5 * base_fitness:
        return stats, "cliff"
    return stats, "robust"


async def run_sensitivity(
    req: SensitivityRequest,
    data_client: DataClient,
    *,
    conn: AsyncConnection | None = None,
) -> SensitivityResponse:
    """拉一次 bars，base + 邻域并发跑引擎，返回敏感性摘要。

    candidate 路径下摘要 merge 进 ``candidate.metrics.sensitivity``（best-effort，
    写失败不阻断响应）。邻域 run 不落 ``backtest_runs``。
    """
    raw_bars = await data_client.get_bars(
        venue=req.venue,
        symbol=req.symbol,
        timeframe=req.timeframe,
        from_ts=req.from_ts,
        to_ts=req.to_ts,
    )
    instrument_id = InstrumentId(symbol=req.symbol, venue=req.venue)
    bars = [_bar_from_dict(b, instrument_id, req.timeframe) for b in raw_bars]
    if not bars:
        raise ValidationError(
            f"data-service returned 0 bars for {req.symbol}@{req.venue}",
            code="NO_BARS_AVAILABLE",
        )

    candidate_code: str | None = None
    if req.candidate_id is not None:
        if conn is None:
            raise ValidationError(
                "candidate_id path requires database connection",
                code="CANDIDATE_NO_DB",
            )
        row = await candidates_store.get_candidate(conn, req.candidate_id)
        if row is None:
            raise NotFoundError(
                f"candidate {req.candidate_id} not found",
                code="CANDIDATE_NOT_FOUND",
            )
        candidate_code = row["code"]
        reaudit = audit_strategy_code(candidate_code)
        if not reaudit.ok:
            raise ValidationError(
                f"candidate {req.candidate_id} failed re-audit: {reaudit.reason()}",
                code="CANDIDATE_REAUDIT_FAILED",
            )

    bars_per_year = float(periods_per_year(req.timeframe))
    combos = build_neighborhood(req.params, pct=req.pct, max_combos=req.max_combos)

    async def _fitness_for(params: dict[str, Any]) -> float | None:
        """单组合跑引擎 → fitness；策略构造/运行报错返 None（非法组合自然过滤）。"""
        try:
            report = await _run_engine(
                bars=bars,
                instrument_id=instrument_id,
                timeframe=req.timeframe,
                strategy_id=req.strategy_id,
                candidate_code=candidate_code,
                params=params,
                initial_cash=req.initial_cash,
                fee_rate=req.fee_rate,
            )
        except Exception:
            return None
        return _fitness_from_report(report, bars_per_year=bars_per_year)

    base_fitness, *neighbor_fitnesses = await asyncio.gather(
        _fitness_for(req.params), *(_fitness_for(c) for c in combos)
    )
    if base_fitness is None:
        raise ValidationError(
            "base params failed to run — fix the strategy/params before checking "
            "sensitivity (pass the FINAL converged params, defaults in source are "
            "not perturbed)",
            code="SENSITIVITY_BASE_FAILED",
        )

    neighbors = [
        SensitivityNeighbor(
            params=combo,
            fitness=fit,
            error=None if fit is not None else "strategy rejected params or runtime error",
        )
        for combo, fit in zip(combos, neighbor_fitnesses, strict=True)
    ]
    stats, verdict = summarize_neighbors(base_fitness, neighbors)

    response = SensitivityResponse(
        candidate_id=req.candidate_id,
        strategy_id=req.strategy_id,
        base_fitness=base_fitness,
        pct=req.pct,
        neighbors=neighbors,
        stats=stats,
        verdict=verdict,
    )

    if conn is not None and req.candidate_id is not None:
        try:
            await candidates_store.update_sensitivity(
                conn,
                req.candidate_id,
                sensitivity={
                    "base_fitness": base_fitness,
                    "pct": req.pct,
                    "stats": stats.model_dump(mode="json"),
                    "verdict": verdict,
                },
            )
        except Exception:  # pragma: no cover - best effort
            import logging

            logging.getLogger(__name__).warning(
                "candidate update_sensitivity failed", exc_info=True
            )

    return response
