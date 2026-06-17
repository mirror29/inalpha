"""WorldQuant 101 Alphas 适配器（可时序计算子集）。

来源：Kakushadze (2015) "101 Formulaic Alphas"（arXiv:1601.00991）。原文多数 alpha 用
横截面 ``rank()``（需多标的 universe），本期单标的择时只实装**可纯时序计算**的子集，
横截面项在 catalog 标 ``needs_universe=true``、本期不计算（见 docs/miro/11 §5）。

纯 pandas/numpy 实现，无第三方依赖，永远可用。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .base import FactorSpec

# 可时序计算子集 ──────────────────────────────────────────────────────
_TS_SPECS: list[FactorSpec] = [
    FactorSpec("alpha101.a101", "alpha101", "Alpha#101 (close-open)/(high-low)", "momentum", direction_hint=1),
    FactorSpec("alpha101.a54", "alpha101", "Alpha#54 price-position structure", "mean_reversion", direction_hint=1),
    FactorSpec("alpha101.a23", "alpha101", "Alpha#23 high reversal", "mean_reversion", direction_hint=-1),
    FactorSpec("alpha101.a12", "alpha101", "Alpha#12 volume-driven reversal", "momentum", direction_hint=1),
    FactorSpec("alpha101.a49", "alpha101", "Alpha#49 trend/reversal switch", "momentum", direction_hint=1),
]

# 横截面项（本期不算，仅在 catalog 露出，标 needs_universe）
_XS_SPECS: list[FactorSpec] = [
    FactorSpec("alpha101.a1", "alpha101", "Alpha#1 cross-sectional rank", "momentum", needs_universe=True),
    FactorSpec("alpha101.a3", "alpha101", "Alpha#3 cross-sectional correlation", "volume", needs_universe=True),
    FactorSpec("alpha101.a6", "alpha101", "Alpha#6 cross-sectional correlation", "volume", needs_universe=True),
]


def _delta(s: pd.Series, n: int) -> pd.Series:
    return s - s.shift(n)


class Alpha101Adapter:
    """WorldQuant 101 alpha 时序子集。"""

    source = "alpha101"

    def available(self) -> bool:
        return True

    def specs(self) -> list[FactorSpec]:
        return _TS_SPECS + _XS_SPECS

    def compute(
        self, df: pd.DataFrame, factor_ids: list[str] | None = None
    ) -> dict[str, pd.Series]:
        want = set(factor_ids) if factor_ids is not None else None

        def need(fid: str) -> bool:
            return want is None or fid in want

        out: dict[str, pd.Series] = {}
        o = df["open"].astype(float)
        h = df["high"].astype(float)
        low = df["low"].astype(float)
        c = df["close"].astype(float)
        v = df["volume"].astype(float)

        if need("alpha101.a101"):
            # Alpha#101 = (close - open) / ((high - low) + 0.001)
            out["alpha101.a101"] = (c - o) / ((h - low) + 0.001)

        if need("alpha101.a54"):
            # Alpha#54 = (-1 * (low - close) * open^5) / ((low - high) * close^5)
            num = -1.0 * (low - c) * np.power(o, 5)
            den = ((low - h) * np.power(c, 5)).replace(0.0, np.nan)
            out["alpha101.a54"] = num / den

        if need("alpha101.a23"):
            # Alpha#23 = (sum(high,20)/20 < high) ? -delta(high,2) : 0
            cond = (h.rolling(20).mean() < h)
            out["alpha101.a23"] = (-1.0 * _delta(h, 2)).where(cond, 0.0)

        if need("alpha101.a12"):
            # Alpha#12 = sign(delta(volume,1)) * (-1 * delta(close,1))
            out["alpha101.a12"] = np.sign(_delta(v, 1)) * (-1.0 * _delta(c, 1))

        if need("alpha101.a49"):
            # Alpha#49（简化时序版）：近 20 根的下行斜率超阈值时反转买入信号
            slope = (c.shift(20) - c.shift(10)) / 10.0 - (c.shift(10) - c) / 10.0
            out["alpha101.a49"] = np.where(slope < -0.1 * c, 1.0, -1.0 * _delta(c, 1))
            out["alpha101.a49"] = pd.Series(out["alpha101.a49"], index=df.index)

        return out
