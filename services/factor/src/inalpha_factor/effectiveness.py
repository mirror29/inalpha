"""因子有效性打分 —— 把"原始因子值"变成"当前真正预测前瞻收益的有效因子"。

自实现（前瞻收益分位 + 时序 Rank IC / ICIR），**不依赖 qlib**，所以 qlib 关闭时
择时有效性仍可用（见 docs/miro/11 §3）。

金融时效性：前瞻收益 ``r[t] = close[t+H]/close[t] - 1`` 只用历史已知 bar，末尾 H 根无
前瞻收益直接丢弃，**绝不使用未来数据当现在**（对齐 CLAUDE.md §3.1）。
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True, slots=True)
class EffResult:
    """单因子有效性结果。"""

    value: float | None
    rank_ic: float
    rank_ic_recent: float  # 近 1/3 样本窗的 Rank IC：与全样本同号≈稳定，反号/趋零≈衰减
    icir: float
    turnover: float  # 1 - spearman(rank(f_t), rank(f_{t-1}))：0≈信号不动，1≈每根 bar 重排
    sample_size: int
    quantile_returns: list[tuple[int, float, int]]  # (q, mean_return, n)
    long_short_return: float
    direction: int
    strength: float
    low_confidence: bool


# rank_ic 绝对值达到该阈值才给非 0 方向；归一化分母（|rank_ic|/_IC_FULL → strength）
_IC_DIRECTION_THRESHOLD = 0.02
_IC_FULL_STRENGTH = 0.05
_ICIR_SEGMENTS = 5
# rank_ic_recent 的"近期"窗口 = 样本的尾部 1/3（ADR-0043 D4，衰减信号用）
_RECENT_FRACTION = 3


def _forward_return(close: pd.Series, horizon: int) -> pd.Series:
    """未来 horizon 根 bar 的累计收益；末尾 horizon 根为 NaN。"""
    return close.shift(-horizon) / close - 1.0


def _rank_ic(factor: pd.Series, fwd: pd.Series) -> tuple[float, int]:
    """时序 Rank IC = spearman(rank(factor), rank(fwd))。返回 (ic, sample_size)。"""
    pair = pd.concat([factor, fwd], axis=1).replace([np.inf, -np.inf], np.nan).dropna()
    n = len(pair)
    if n < 3:
        return 0.0, n
    fr = pair.iloc[:, 0].rank()
    rr = pair.iloc[:, 1].rank()
    if fr.std(ddof=0) == 0 or rr.std(ddof=0) == 0:
        return 0.0, n
    ic = float(fr.corr(rr))
    if np.isnan(ic):
        return 0.0, n
    return ic, n


def _icir(factor: pd.Series, fwd: pd.Series, segments: int) -> float:
    """分段 IC 的均值/标准差（稳定性）。"""
    pair = pd.concat([factor, fwd], axis=1).replace([np.inf, -np.inf], np.nan).dropna()
    if len(pair) < segments * 3:
        return 0.0
    bounds = np.linspace(0, len(pair), segments + 1, dtype=int)
    ics: list[float] = []
    for i in range(segments):
        ch = pair.iloc[bounds[i] : bounds[i + 1]]
        if len(ch) < 3:
            continue
        fr = ch.iloc[:, 0].rank()
        rr = ch.iloc[:, 1].rank()
        if fr.std(ddof=0) == 0 or rr.std(ddof=0) == 0:
            continue
        ic = fr.corr(rr)
        if not np.isnan(ic):
            ics.append(float(ic))
    if len(ics) < 2:
        return 0.0
    arr = np.array(ics)
    sd = arr.std(ddof=1)
    if sd == 0:
        return 0.0
    return float(arr.mean() / sd)


def _recent_rank_ic(factor: pd.Series, fwd: pd.Series) -> float:
    """尾部 1/3 样本窗的 Rank IC（先对齐去 NaN 再切尾，保证窗内样本量）。"""
    pair = pd.concat([factor, fwd], axis=1).replace([np.inf, -np.inf], np.nan).dropna()
    tail = len(pair) // _RECENT_FRACTION
    if tail < 3:
        return 0.0
    ch = pair.iloc[-tail:]
    fr = ch.iloc[:, 0].rank()
    rr = ch.iloc[:, 1].rank()
    if fr.std(ddof=0) == 0 or rr.std(ddof=0) == 0:
        return 0.0
    ic = float(fr.corr(rr))
    return 0.0 if np.isnan(ic) else ic


def _turnover(factor: pd.Series) -> float:
    """因子 rank 自相关的补：1 - spearman(f_t, f_{t-1})，截到 [0, 1]。"""
    f = factor.replace([np.inf, -np.inf], np.nan).dropna()
    if len(f) < 3:
        return 0.0
    cur, prev = f.iloc[1:], f.shift(1).iloc[1:]
    fr, pr = cur.rank(), prev.rank()
    if fr.std(ddof=0) == 0 or pr.std(ddof=0) == 0:
        return 0.0
    ac = fr.corr(pr)
    if np.isnan(ac):
        return 0.0
    return float(min(1.0, max(0.0, 1.0 - ac)))


def _quantile_returns(
    factor: pd.Series, fwd: pd.Series, quantiles: int
) -> tuple[list[tuple[int, float, int]], float]:
    """因子值分位 → 各组前瞻收益均值；long_short = top - bottom。"""
    pair = pd.concat([factor.rename("f"), fwd.rename("r")], axis=1)
    pair = pair.replace([np.inf, -np.inf], np.nan).dropna()
    if len(pair) < quantiles * 3:
        return [], 0.0
    try:
        labels = pd.qcut(pair["f"], quantiles, labels=False, duplicates="drop")
    except (ValueError, IndexError):
        return [], 0.0
    pair = pair.assign(q=labels)
    stats: list[tuple[int, float, int]] = []
    grp = pair.groupby("q")["r"]
    for q, sub in grp:
        stats.append((int(q), float(sub.mean()), len(sub)))
    if not stats:
        return [], 0.0
    stats.sort(key=lambda t: t[0])
    long_short = stats[-1][1] - stats[0][1]
    return stats, float(long_short)


def score_factor(
    factor: pd.Series,
    close: pd.Series,
    *,
    horizon: int,
    quantiles: int,
    min_samples: int,
) -> EffResult:
    """对单因子算完整有效性。

    Args:
        factor: 因子时序（与 close 同 index，含 warmup NaN）。
        close: 收盘价时序。
        horizon: 前瞻收益窗口（bar 数）。
        quantiles: 分位组数。
        min_samples: 低于此样本数标 low_confidence。
    """
    fwd = _forward_return(close, horizon)
    rank_ic, n = _rank_ic(factor, fwd)
    rank_ic_recent = _recent_rank_ic(factor, fwd)
    icir = _icir(factor, fwd, _ICIR_SEGMENTS)
    turnover = _turnover(factor)
    qstats, long_short = _quantile_returns(factor, fwd, quantiles)

    low_conf = n < min_samples
    direction = 0
    if not low_conf and abs(rank_ic) >= _IC_DIRECTION_THRESHOLD:
        direction = 1 if rank_ic > 0 else -1
    strength = float(min(1.0, abs(rank_ic) / _IC_FULL_STRENGTH))

    # 最新因子值（最后一个非 NaN）
    valid = factor.replace([np.inf, -np.inf], np.nan).dropna()
    value = float(valid.iloc[-1]) if len(valid) else None

    return EffResult(
        value=value,
        rank_ic=rank_ic,
        rank_ic_recent=rank_ic_recent,
        icir=icir,
        turnover=turnover,
        sample_size=n,
        quantile_returns=qstats,
        long_short_return=long_short,
        direction=direction,
        strength=strength,
        low_confidence=low_conf,
    )
