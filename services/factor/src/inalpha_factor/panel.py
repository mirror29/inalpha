"""横截面 Panel 工具。

多标的对齐 + 横截面 rank-IC + 最新横截面排名。**纯函数**（不取数），engine 负责
逐标的拉 bar 后喂进来。与 effectiveness.py 的**单标的时序** rank-IC 是正交的另一维：
那边是"一个标的的因子时序 vs 它自己的前瞻收益"，这里是"某时刻全池按因子排序 vs
跨标的前瞻收益"（量化界因子有效性的标准口径）。

金融时效性（§3.1）：前瞻收益 ``close[t+H]/close[t]-1`` 只用历史 bar，末 H 行 NaN，
绝不用未来数据当现在；横向对齐缺口留 NaN，**不前向填充制造假成交**。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

#: 某期横截面有效（非 NaN）标的数低于此 → 该期不排名（残缺池排名是伪信号）
DEFAULT_MIN_SYMBOLS = 3
#: 横截面 IC 的有效期数低于此 → low_confidence（均值/ICIR 不可靠）
MIN_XS_PERIODS = 20


def align_field(frames: dict[str, pd.DataFrame], field: str) -> pd.DataFrame:
    """per-symbol OHLCV → 单字段的 time × symbol 面板。

    外连接到所有标的时间索引的并集；某标的缺该时刻 → NaN（不 ffill 制造假成交）。
    空 / 缺列的标的整列略过；重复时间戳保留最后一条。
    """
    cols: dict[str, pd.Series] = {}
    for sym, df in frames.items():
        if df is None or df.empty or field not in df.columns:
            continue
        s = df[field].astype(float)
        s = s[~s.index.duplicated(keep="last")]
        if len(s):
            cols[sym] = s
    if not cols:
        return pd.DataFrame()
    return pd.DataFrame(cols).sort_index()


def cross_sectional_rank(panel: pd.DataFrame) -> pd.DataFrame:
    """每行（时刻）对各 symbol 做横截面百分位 rank ∈ (0,1]；全 NaN 行保持 NaN。

    内禀横截面因子（WorldQuant ``rank()``）的基础算子：某标的的值取决于当天它在
    全池里的相对位置。NaN（缺观测）不参与排名、结果保持 NaN，不冒充。
    """
    if panel.empty:
        return panel
    return panel.rank(axis=1, pct=True)


def forward_return_panel(close_panel: pd.DataFrame, horizon: int) -> pd.DataFrame:
    """每标的前瞻收益 ``close[t+H]/close[t]-1``；末 H 行 NaN（绝不用未来，§3.1）。"""
    if close_panel.empty:
        return close_panel
    shifted = close_panel.shift(-horizon)
    return shifted / close_panel.replace(0.0, np.nan) - 1.0


def cross_sectional_ic(
    factor_panel: pd.DataFrame,
    fwd_panel: pd.DataFrame,
    *,
    min_symbols: int = DEFAULT_MIN_SYMBOLS,
) -> tuple[float, float, int, float]:
    """逐期横截面 rank-IC = ``spearman over symbols(factor_t, fwd_t)``。

    Args:
        factor_panel: time × symbol 因子值。
        fwd_panel: time × symbol 前瞻收益。
        min_symbols: 某期有效标的数下限；不足则跳过该期。

    Returns:
        ``(mean_ic, icir, n_periods, mean_valid_symbols)`` —— 横截面 IC 均值、
        IC 序列的 mean/std（稳定性）、参与的期数、每期平均有效标的数。
        无任何有效期 → 全 0。

    **向量化**（每行一次性算，不再逐期 ``.loc[t]`` Python 循环）：spearman = 行内
    rank 后的 pearson；只在两边都有值的标的上 rank，行有效标的 < min_symbols 或
    rank 全平（方差 0）的行剔除。52 因子 × 数百期时这比逐期循环快一个量级。
    """
    if factor_panel.empty or fwd_panel.empty:
        return 0.0, 0.0, 0, 0.0
    idx = factor_panel.index.intersection(fwd_panel.index)
    cols = factor_panel.columns.intersection(fwd_panel.columns)
    if len(idx) == 0 or len(cols) == 0:
        return 0.0, 0.0, 0, 0.0
    f = factor_panel.loc[idx, cols].replace([np.inf, -np.inf], np.nan)
    r = fwd_panel.loc[idx, cols].replace([np.inf, -np.inf], np.nan)
    # 只在 (因子, 前瞻收益) 都有值的格子上参与（逐期 dropna 的向量化等价）
    both = f.notna() & r.notna()
    keep = both.sum(axis=1) >= min_symbols
    if not keep.any():
        return 0.0, 0.0, 0, 0.0
    both = both[keep]
    fr = f[keep].where(both).rank(axis=1)
    rr = r[keep].where(both).rank(axis=1)
    # 行内 pearson(rank) = spearman；中心化后逐行点积 / 模长
    frm = fr.sub(fr.mean(axis=1), axis=0)
    rrm = rr.sub(rr.mean(axis=1), axis=0)
    den = np.sqrt((frm**2).sum(axis=1) * (rrm**2).sum(axis=1))
    ic_row = ((frm * rrm).sum(axis=1) / den.replace(0.0, np.nan)).dropna()
    if ic_row.empty:
        return 0.0, 0.0, 0, 0.0
    valid_counts = both.sum(axis=1).loc[ic_row.index]
    arr = ic_row.to_numpy()
    sd = arr.std(ddof=1) if len(arr) > 1 else 0.0
    icir = float(arr.mean() / sd) if sd > 0 else 0.0
    return float(arr.mean()), icir, len(arr), float(valid_counts.mean())


def latest_cross_section(
    factor_panel: pd.DataFrame, *, min_symbols: int = DEFAULT_MIN_SYMBOLS
) -> tuple[pd.Timestamp | None, list[tuple[str, float, float]]]:
    """最近一个有效横截面（有效标的数 ≥ min_symbols 的最后一行）的排名。

    返回 ``(t, [(symbol, value, rank_pct)])``，按 value 升序——给选标的直接用
    （如取 PB 最低者 = 列表第一个；动量最强 = 最后一个）。rank_pct ∈ (0,1]。
    无有效横截面 → ``(None, [])``。
    """
    for t in reversed(factor_panel.index):
        row = factor_panel.loc[t].replace([np.inf, -np.inf], np.nan).dropna()
        if len(row) < min_symbols:
            continue
        ranks = row.rank(pct=True)
        out = [(str(sym), float(row[sym]), float(ranks[sym])) for sym in row.index]
        out.sort(key=lambda x: x[1])
        return t, out
    return None, []
