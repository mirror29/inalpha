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
    decay_state: str  # stable / fading / decaying（见 decay_state()，ADR-0047 单一权威）


# rank_ic 绝对值达到该阈值才给非 0 方向；归一化分母（|rank_ic|/_IC_FULL → strength）
_IC_DIRECTION_THRESHOLD = 0.02
_IC_FULL_STRENGTH = 0.05
_ICIR_SEGMENTS = 5
# rank_ic_recent 的"近期"窗口 = 样本的尾部 1/3（ADR-0043 D4，衰减信号用）
_RECENT_FRACTION = 3
# 衰减三态的 stable 保留比：|recent| ≥ 0.6·|ic| 算稳。钉死不进配置——
# 还没有样本支撑可调性（ADR-0047 D2）；前端原 decayState() 同阈值，下沉后以此为准。
_DECAY_STABLE_RETENTION = 0.6


def decay_state(rank_ic: float, rank_ic_recent: float) -> str:
    """衰减三态判定（ADR-0047 D2，单一权威——前端/巡检都读本结果不再自算）。

    - ``decaying``：recent 为 0 或与全样本 IC 反号（含 ic=0 而 recent≠0 的退化对）
    - ``stable``：量级保住 ``_DECAY_STABLE_RETENTION`` 以上
    - ``fading``：其间（保住 0~60%，走弱中）
    """
    if rank_ic_recent == 0.0 or np.sign(rank_ic_recent) != np.sign(rank_ic):
        return "decaying"
    if abs(rank_ic_recent) >= _DECAY_STABLE_RETENTION * abs(rank_ic):
        return "stable"
    return "fading"


def _forward_return(close: pd.Series, horizon: int) -> pd.Series:
    """未来 horizon 根 bar 的累计收益；末尾 horizon 根为 NaN。"""
    return close.shift(-horizon) / close - 1.0


# Euler–Mascheroni 常数（E[max] 渐近近似用）
_EULER_GAMMA = 0.5772156649015329


def null_ic_benchmark(n_candidates: int, sample_size: int, horizon: int) -> float:
    """纯噪声下 N 个候选里期望最大 |IC|（选择效应基准，ADR-0043 D4 延伸）。

    Bailey–López de Prado 的 E[max] 渐近近似：

    - ``n_eff = max(4, sample_size // horizon)`` —— 前瞻收益按 horizon 重叠，
      独立样本数保守按 1/horizon 折算（启发式，非严格）
    - 单个零假设 IC 的标准差 ``σ ≈ 1/√(n_eff − 1)``
    - ``E[max|null] ≈ σ·[(1−γ)·Φ⁻¹(1−1/N) + γ·Φ⁻¹(1−1/(N·e))]``，γ=Euler 常数

    **读法**：top 因子 |rank_ic| 不显著高于该值 ⇒ 可能只是从 N 个候选里挑出来的
    选择效应，不是真信号。已知局限：n_eff 折算是启发式；近似假设候选间独立
    （实际正相关 → 真实基准略低）——它是**地板不是假设检验**。只透出供 LLM/人
    判断，不设阈值不剔除（ADR-0043 推迟决议不变）。
    """
    if n_candidates < 1 or sample_size < 1:
        return 0.0
    n_eff = max(4, sample_size // max(1, horizon))
    sigma = 1.0 / np.sqrt(n_eff - 1)
    if n_candidates == 1:
        # 单候选无选择效应：基准即单次抽样的 ~0 期望，给 σ 量级便于解读
        return float(sigma)
    from statistics import NormalDist

    inv = NormalDist().inv_cdf
    e_max = (1 - _EULER_GAMMA) * inv(1 - 1.0 / n_candidates) + _EULER_GAMMA * inv(
        1 - 1.0 / (n_candidates * np.e)
    )
    return float(sigma * e_max)


def ic_pvalue(ic: float, sample_size: int, horizon: int = 1) -> float:
    """Rank IC 的双侧 p 值（t 近似 + 大样本正态；与 null_ic_benchmark 同款 n_eff 折算）。

    ``t = ic·√((n_eff−2)/(1−ic²))``。局限同 :func:`null_ic_benchmark`：1/horizon
    折算是启发式，p 值是参考量级不是严格检验——L1 pipeline 的 BH 校正消费它做
    批内排序/粗筛，不做学术意义上的显著性宣称。
    """
    n_eff = max(4, sample_size // max(1, horizon))
    ic = float(min(0.999999, max(-0.999999, ic)))
    t = abs(ic) * np.sqrt((n_eff - 2) / (1.0 - ic * ic))
    from statistics import NormalDist

    return float(2.0 * (1.0 - NormalDist().cdf(t)))


def bh_adjust(pvalues: list[float]) -> list[float]:
    """Benjamini–Hochberg 校正（FDR），返回与输入同序的调整后 p 值。

    L1 因子发现 pipeline 的批内强制步骤（ADR-0019 关键约定 4）：一批评估 m 个
    候选表达式时，原始 p 值必须按 m 校正再做 propose 排序，挡"试 30 个总有一个
    p<0.05"的多重检验作弊。~20 行 numpy，不引 scipy。
    """
    m = len(pvalues)
    if m == 0:
        return []
    p = np.asarray(pvalues, dtype=float)
    order = np.argsort(p)
    ranked = p[order] * m / (np.arange(m) + 1)
    # 从大到小取累计最小，保证调整后 p 值单调
    ranked = np.minimum.accumulate(ranked[::-1])[::-1]
    out = np.empty(m, dtype=float)
    out[order] = np.clip(ranked, 0.0, 1.0)
    return [float(v) for v in out]


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
    """因子 rank 自相关的补：1 - spearman(f_t, f_{t-1})，截到 [0, 1]。

    配对必须**先 shift 再 dropna**:序列带内洞(macro 因子超 staleness 上限的
    NaN 段)时,先 dropna 会把缺口折叠,shift(1) 把跨缺口的两个值当相邻时间步,
    换手被错算。先按原索引取 t-1,再只保留 (f_t, f_{t-1}) 都有值的真相邻对——
    价量因子(只有 warmup 前缀 NaN)结果不变,带洞序列跨缺口对被剔除。
    """
    f = factor.replace([np.inf, -np.inf], np.nan)
    pair = pd.concat([f.rename("cur"), f.shift(1).rename("prev")], axis=1).dropna()
    if len(pair) < 3:
        return 0.0
    fr, pr = pair["cur"].rank(), pair["prev"].rank()
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
        decay_state=decay_state(rank_ic, rank_ic_recent),
    )
