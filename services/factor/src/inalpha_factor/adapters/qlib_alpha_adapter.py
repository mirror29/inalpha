"""qlib Alpha158 风格因子适配器（纯 pandas 实现，FACTOR_QLIB_ENABLED 开关）。

来源：Microsoft qlib ``Alpha158``（K 线形态 + 滚动统计因子集）。因子值用与
Alpha158 等价的 pandas 公式本地计算，**不依赖 pyqlib**（ADR-0043 D1：旧版的
``import qlib`` 门对计算零贡献，已移除；``qlib`` extra 留给将来接原生表达式
引擎 / 离线数据目录时用）。``FACTOR_QLIB_ENABLED`` 开关保留作降级阀门，默认开。

因子家族 × 窗口 {5, 20, 60}（ADR-0043 D2：不全取 Alpha158 的 5 个窗口，
避免候选爆炸加重多重检验）：

- K 线形态：KMID / KLEN / KUP / KLOW
- 动量：ROC / RSV / CNTP / CNTN / SUMP
- 波动：STD
- 趋势：BETA / RSQR
- 均值回归：MAX / MIN / QTLU / QTLD
- 量价：CORR（价量相关）/ VMA（量相对均线）/ VSTD（量变异系数）
"""
from __future__ import annotations

from collections.abc import Callable

import numpy as np
import pandas as pd

from .base import FactorSpec

_SPECS: list[FactorSpec] = [
    # ── K 线形态（单根 bar，无窗口）─────────────────────────────────
    FactorSpec("qlib.kmid", "qlib_alpha158", "KMID (close-open)/open", "momentum", direction_hint=1),
    FactorSpec("qlib.klen", "qlib_alpha158", "KLEN (high-low)/open range", "volatility"),
    FactorSpec("qlib.kup", "qlib_alpha158", "KUP upper-shadow ratio", "mean_reversion", direction_hint=-1),
    FactorSpec("qlib.klow", "qlib_alpha158", "KLOW lower-shadow ratio", "mean_reversion", direction_hint=1),
    # ── ROC 动量 ─────────────────────────────────────────────────────
    FactorSpec("qlib.roc_5", "qlib_alpha158", "ROC(5) close/Ref(close,5)", "momentum", direction_hint=1),
    FactorSpec("qlib.roc_20", "qlib_alpha158", "ROC(20) close/Ref(close,20)", "momentum", direction_hint=1),
    FactorSpec("qlib.roc_60", "qlib_alpha158", "ROC(60) close/Ref(close,60)", "momentum", direction_hint=1),
    # ── STD 波动率 ───────────────────────────────────────────────────
    FactorSpec("qlib.std_5", "qlib_alpha158", "STD(5)/close volatility", "volatility"),
    FactorSpec("qlib.std_20", "qlib_alpha158", "STD(20)/close volatility", "volatility"),
    FactorSpec("qlib.std_60", "qlib_alpha158", "STD(60)/close volatility", "volatility"),
    # ── BETA / RSQR 趋势 ─────────────────────────────────────────────
    FactorSpec("qlib.beta_20", "qlib_alpha158", "BETA(20) close slope/close", "trend", direction_hint=1),
    FactorSpec("qlib.beta_60", "qlib_alpha158", "BETA(60) close slope/close", "trend", direction_hint=1),
    FactorSpec("qlib.rsqr_20", "qlib_alpha158", "RSQR(20) linear-fit R²", "trend"),
    # ── MAX / MIN 距高低点 ───────────────────────────────────────────
    FactorSpec("qlib.max_20", "qlib_alpha158", "MAX(20)/close distance to high", "mean_reversion", direction_hint=-1),
    FactorSpec("qlib.max_60", "qlib_alpha158", "MAX(60)/close distance to high", "mean_reversion", direction_hint=-1),
    FactorSpec("qlib.min_20", "qlib_alpha158", "MIN(20)/close distance to low", "mean_reversion", direction_hint=1),
    FactorSpec("qlib.min_60", "qlib_alpha158", "MIN(60)/close distance to low", "mean_reversion", direction_hint=1),
    # ── QTLU / QTLD 滚动分位 ─────────────────────────────────────────
    FactorSpec("qlib.qtlu_20", "qlib_alpha158", "QTLU(20) 80th pctl/close", "mean_reversion"),
    FactorSpec("qlib.qtld_20", "qlib_alpha158", "QTLD(20) 20th pctl/close", "mean_reversion"),
    # ── RSV 区间位置 ─────────────────────────────────────────────────
    FactorSpec("qlib.rsv_5", "qlib_alpha158", "RSV(5) close position in range", "momentum"),
    FactorSpec("qlib.rsv_20", "qlib_alpha158", "RSV(20) close position in range", "momentum"),
    # ── CORR 价量相关 ────────────────────────────────────────────────
    FactorSpec("qlib.corr_20", "qlib_alpha158", "CORR(20) close×log(volume) corr", "volume"),
    FactorSpec("qlib.corr_60", "qlib_alpha158", "CORR(60) close×log(volume) corr", "volume"),
    # ── CNTP / CNTN 涨跌占比 ─────────────────────────────────────────
    FactorSpec("qlib.cntp_20", "qlib_alpha158", "CNTP(20) up-bar ratio", "momentum", direction_hint=1),
    FactorSpec("qlib.cntp_60", "qlib_alpha158", "CNTP(60) up-bar ratio", "momentum", direction_hint=1),
    FactorSpec("qlib.cntn_20", "qlib_alpha158", "CNTN(20) down-bar ratio", "momentum", direction_hint=-1),
    # ── SUMP 涨幅占比（RSI 型）──────────────────────────────────────
    FactorSpec("qlib.sump_20", "qlib_alpha158", "SUMP(20) gains/total-range RSI-style", "momentum"),
    FactorSpec("qlib.sump_60", "qlib_alpha158", "SUMP(60) gains/total-range RSI-style", "momentum"),
    # ── VMA / VSTD 量能 ──────────────────────────────────────────────
    FactorSpec("qlib.vma_20", "qlib_alpha158", "VMA(20) volume/volume-MA", "volume"),
    FactorSpec("qlib.vstd_20", "qlib_alpha158", "VSTD(20) volume coeff of variation", "volume"),
]


class QlibAlphaAdapter:
    """qlib Alpha158 风格因子源（纯 pandas，默认启用）。"""

    source = "qlib_alpha158"

    def __init__(self, enabled: bool = True) -> None:
        self._enabled = enabled

    def available(self) -> bool:
        # 纯 pandas 实现，无库依赖（ADR-0043 D1）——只受开关控制
        return self._enabled

    def specs(self) -> list[FactorSpec]:
        return _SPECS

    def compute(
        self, df: pd.DataFrame, factor_ids: list[str] | None = None
    ) -> dict[str, pd.Series]:
        if not self.available():
            return {}
        want = set(factor_ids) if factor_ids is not None else None

        o = df["open"].astype(float)
        h = df["high"].astype(float)
        low = df["low"].astype(float)
        c = df["close"].astype(float)
        v = df["volume"].astype(float)
        o_safe = o.replace(0.0, np.nan)
        c_safe = c.replace(0.0, np.nan)
        ret = c.diff()
        log_v = np.log(v.clip(lower=0.0) + 1.0)

        def roc(w: int) -> pd.Series:
            return c / c.shift(w).replace(0.0, np.nan) - 1.0

        def std(w: int) -> pd.Series:
            return c.rolling(w).std() / c_safe

        def max_(w: int) -> pd.Series:
            return c / h.rolling(w).max().replace(0.0, np.nan) - 1.0

        def min_(w: int) -> pd.Series:
            return c / low.rolling(w).min().replace(0.0, np.nan) - 1.0

        def qtl(w: int, q: float) -> pd.Series:
            return c.rolling(w).quantile(q) / c_safe

        def rsv(w: int) -> pd.Series:
            lo = low.rolling(w).min()
            span = (h.rolling(w).max() - lo).replace(0.0, np.nan)
            return (c - lo) / span

        def corr(w: int) -> pd.Series:
            return c.rolling(w).corr(log_v)

        def cnt(w: int, up: bool) -> pd.Series:
            cond = (ret > 0) if up else (ret < 0)
            return cond.astype(float).rolling(w).mean()

        def sump(w: int) -> pd.Series:
            gain = ret.clip(lower=0.0).rolling(w).sum()
            total = ret.abs().rolling(w).sum().replace(0.0, np.nan)
            return gain / total

        linfit_cache: dict[int, tuple[pd.Series, pd.Series]] = {}

        def linfit(w: int) -> tuple[pd.Series, pd.Series]:
            if w not in linfit_cache:
                linfit_cache[w] = self._rolling_linfit(c, w)
            return linfit_cache[w]

        def beta(w: int) -> pd.Series:
            return linfit(w)[0] / c_safe

        def rsqr(w: int) -> pd.Series:
            return linfit(w)[1]

        formulas: dict[str, Callable[[], pd.Series]] = {
            "qlib.kmid": lambda: (c - o) / o_safe,
            "qlib.klen": lambda: (h - low) / o_safe,
            "qlib.kup": lambda: (h - np.maximum(o, c)) / o_safe,
            "qlib.klow": lambda: (np.minimum(o, c) - low) / o_safe,
            "qlib.roc_5": lambda: roc(5),
            "qlib.roc_20": lambda: roc(20),
            "qlib.roc_60": lambda: roc(60),
            "qlib.std_5": lambda: std(5),
            "qlib.std_20": lambda: std(20),
            "qlib.std_60": lambda: std(60),
            "qlib.beta_20": lambda: beta(20),
            "qlib.beta_60": lambda: beta(60),
            "qlib.rsqr_20": lambda: rsqr(20),
            "qlib.max_20": lambda: max_(20),
            "qlib.max_60": lambda: max_(60),
            "qlib.min_20": lambda: min_(20),
            "qlib.min_60": lambda: min_(60),
            "qlib.qtlu_20": lambda: qtl(20, 0.8),
            "qlib.qtld_20": lambda: qtl(20, 0.2),
            "qlib.rsv_5": lambda: rsv(5),
            "qlib.rsv_20": lambda: rsv(20),
            "qlib.corr_20": lambda: corr(20),
            "qlib.corr_60": lambda: corr(60),
            "qlib.cntp_20": lambda: cnt(20, up=True),
            "qlib.cntp_60": lambda: cnt(60, up=True),
            "qlib.cntn_20": lambda: cnt(20, up=False),
            "qlib.sump_20": lambda: sump(20),
            "qlib.sump_60": lambda: sump(60),
            "qlib.vma_20": lambda: v / v.rolling(20).mean().replace(0.0, np.nan),
            "qlib.vstd_20": lambda: v.rolling(20).std()
            / v.rolling(20).mean().replace(0.0, np.nan),
        }

        out: dict[str, pd.Series] = {}
        for fid, fn in formulas.items():
            if want is None or fid in want:
                out[fid] = fn()
        return out

    @staticmethod
    def _rolling_linfit(s: pd.Series, window: int) -> tuple[pd.Series, pd.Series]:
        """滚动线性回归：返回 (slope, R²)，对齐 Alpha158 BETA / RSQR。"""
        x = np.arange(window, dtype=float)
        x_mean = x.mean()
        x_dev = x - x_mean
        x_var = (x_dev**2).sum()

        def _slope(win: np.ndarray) -> float:
            if np.isnan(win).any():
                return np.nan
            y_dev = win - win.mean()
            return float((x_dev * y_dev).sum() / x_var)

        def _r2(win: np.ndarray) -> float:
            if np.isnan(win).any():
                return np.nan
            y_mean = win.mean()
            y_dev = win - y_mean
            ss_tot = (y_dev**2).sum()
            if ss_tot == 0:
                return np.nan
            slope = (x_dev * y_dev).sum() / x_var
            pred = slope * x_dev
            ss_res = ((y_dev - pred) ** 2).sum()
            return float(1.0 - ss_res / ss_tot)

        slope = s.rolling(window).apply(_slope, raw=True)
        r2 = s.rolling(window).apply(_r2, raw=True)
        return slope, r2
