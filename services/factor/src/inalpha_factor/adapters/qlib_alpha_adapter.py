"""qlib Alpha158 风格因子适配器（FACTOR_QLIB_ENABLED 开关 + import 守卫）。

来源：Microsoft qlib ``Alpha158``（K 线形态 + 滚动统计因子集）。pyqlib 体量大、
Apple Silicon 偶有装机坑，所以**默认关闭**（``FACTOR_QLIB_ENABLED=false``）；关闭或库
未装时 ``available()`` 返 False，catalog 里这些因子标 ``available=false``，pandas-ta +
alpha101 仍可独立工作（见 docs/miro/11 §5）。

实现说明：Alpha158 因子是 OHLCV 上的**公式化定义**（KMID/KLEN/ROC/STD/BETA/RSQR...）。
启用时本适配器先确认 ``import qlib`` 成功（证明环境已就绪），因子值用与 Alpha158 等价的
pandas 公式在本地算（不拉 qlib 的离线数据目录，避免 MVP 引入 qlib 全套数据基建）。
这与 docs/miro/11 §5 "有效性自实现、不绑 qlib 全家桶" 的取舍一致；后续需要 qlib 原生
表达式引擎时可在此切换。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .base import FactorSpec

try:
    import qlib as _qlib  # noqa: F401

    _HAS_QLIB = True
except Exception:  # pragma: no cover - 取决于是否 uv sync --extra qlib
    _HAS_QLIB = False


_SPECS: list[FactorSpec] = [
    FactorSpec("qlib.kmid", "qlib_alpha158", "KMID (close-open)/open", "momentum", direction_hint=1),
    FactorSpec("qlib.klen", "qlib_alpha158", "KLEN (high-low)/open 波幅", "volatility"),
    FactorSpec("qlib.kup", "qlib_alpha158", "KUP 上影线占比", "mean_reversion", direction_hint=-1),
    FactorSpec("qlib.klow", "qlib_alpha158", "KLOW 下影线占比", "mean_reversion", direction_hint=1),
    FactorSpec("qlib.roc_20", "qlib_alpha158", "ROC(20) close/Ref(close,20)", "momentum", direction_hint=1),
    FactorSpec("qlib.std_20", "qlib_alpha158", "STD(20)/close 波动率", "volatility"),
    FactorSpec("qlib.beta_20", "qlib_alpha158", "BETA(20) 收盘价斜率/close", "trend", direction_hint=1),
    FactorSpec("qlib.rsqr_20", "qlib_alpha158", "RSQR(20) 线性拟合 R²", "trend"),
    FactorSpec("qlib.max_20", "qlib_alpha158", "MAX(20)/close 距高点", "mean_reversion", direction_hint=-1),
    FactorSpec("qlib.min_20", "qlib_alpha158", "MIN(20)/close 距低点", "mean_reversion", direction_hint=1),
]


class QlibAlphaAdapter:
    """qlib Alpha158 风格因子源（默认关闭）。"""

    source = "qlib_alpha158"

    def __init__(self, enabled: bool = False) -> None:
        self._enabled = enabled

    def available(self) -> bool:
        return self._enabled and _HAS_QLIB

    def specs(self) -> list[FactorSpec]:
        return _SPECS

    def compute(
        self, df: pd.DataFrame, factor_ids: list[str] | None = None
    ) -> dict[str, pd.Series]:
        if not self.available():
            return {}
        want = set(factor_ids) if factor_ids is not None else None

        def need(fid: str) -> bool:
            return want is None or fid in want

        out: dict[str, pd.Series] = {}
        o = df["open"].astype(float)
        h = df["high"].astype(float)
        low = df["low"].astype(float)
        c = df["close"].astype(float)
        o_safe = o.replace(0.0, np.nan)
        c_safe = c.replace(0.0, np.nan)

        if need("qlib.kmid"):
            out["qlib.kmid"] = (c - o) / o_safe
        if need("qlib.klen"):
            out["qlib.klen"] = (h - low) / o_safe
        if need("qlib.kup"):
            out["qlib.kup"] = (h - np.maximum(o, c)) / o_safe
        if need("qlib.klow"):
            out["qlib.klow"] = (np.minimum(o, c) - low) / o_safe
        if need("qlib.roc_20"):
            out["qlib.roc_20"] = c / c.shift(20).replace(0.0, np.nan) - 1.0
        if need("qlib.std_20"):
            out["qlib.std_20"] = c.rolling(20).std() / c_safe
        if need("qlib.beta_20") or need("qlib.rsqr_20"):
            beta, rsqr = self._rolling_linfit(c, 20)
            if need("qlib.beta_20"):
                out["qlib.beta_20"] = beta / c_safe
            if need("qlib.rsqr_20"):
                out["qlib.rsqr_20"] = rsqr
        if need("qlib.max_20"):
            out["qlib.max_20"] = c / h.rolling(20).max().replace(0.0, np.nan) - 1.0
        if need("qlib.min_20"):
            out["qlib.min_20"] = c / low.rolling(20).min().replace(0.0, np.nan) - 1.0

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
