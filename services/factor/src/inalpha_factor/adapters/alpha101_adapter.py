"""WorldQuant 101 Alphas 适配器（可时序计算子集）。

来源：Kakushadze (2015) "101 Formulaic Alphas"（arXiv:1601.00991）。原文多数 alpha 用
横截面 ``rank()``（需多标的 universe），本期单标的择时只实装**可纯时序计算**的子集，
横截面项在 catalog 标 ``needs_universe=true``、本期不计算（见 docs/miro/11 §5）。

纯 pandas/numpy 实现，无第三方依赖，永远可用。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..panel import cross_sectional_rank
from .base import FactorSpec

# 可时序计算子集 ──────────────────────────────────────────────────────
_TS_SPECS: list[FactorSpec] = [
    FactorSpec("alpha101.a101", "alpha101", "Alpha#101 (close-open)/(high-low)", "momentum", direction_hint=1),
    FactorSpec("alpha101.a54", "alpha101", "Alpha#54 price-position structure", "mean_reversion", direction_hint=1),
    FactorSpec("alpha101.a23", "alpha101", "Alpha#23 high reversal", "mean_reversion", direction_hint=-1),
    FactorSpec("alpha101.a12", "alpha101", "Alpha#12 volume-driven reversal", "momentum", direction_hint=1),
    FactorSpec("alpha101.a49", "alpha101", "Alpha#49 trend/reversal switch", "momentum", direction_hint=1),
    # Alpha#6 = -corr(open, volume, 10)：纯时序（无 rank），原误标 needs_universe，
    # ADR-0055 评审纠正下放到时序子集并实装
    FactorSpec("alpha101.a6", "alpha101", "Alpha#6 -corr(open,volume,10)", "volume", direction_hint=-1),
]

# 横截面项（本期不算，仅在 catalog 露出，标 needs_universe）：a1 含 rank()、
# a3 = -corr(rank(open),rank(volume),10) 含横截面 rank，二者确需 universe（ADR-0055）
_XS_SPECS: list[FactorSpec] = [
    FactorSpec("alpha101.a1", "alpha101", "Alpha#1 cross-sectional rank", "momentum", needs_universe=True),
    FactorSpec("alpha101.a3", "alpha101", "Alpha#3 cross-sectional correlation", "volume", needs_universe=True),
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

        if need("alpha101.a6"):
            # Alpha#6 = -1 * correlation(open, volume, 10)，纯时序滚动相关
            out["alpha101.a6"] = -1.0 * o.rolling(10).corr(v)

        return out

    # ── 内禀横截面因子（ADR-0055 D1 ①：含 rank()，必须喂多标的 Panel）──────
    def compute_cross_sectional(
        self,
        fields: dict[str, pd.DataFrame],
        factor_ids: list[str] | None = None,
    ) -> dict[str, pd.DataFrame]:
        """在多标的 Panel 上算 ``needs_universe`` 横截面 alpha → time × symbol 矩阵。

        Args:
            fields: OHLCV 字段名 → time × symbol 面板（engine 用 align_field 组装）。
            factor_ids: 只算这些；None = 本源全部横截面因子。

        Returns:
            factor_id -> time × symbol 因子矩阵。缺所需字段 / 空面板时跳过对应 id。
        """
        want = set(factor_ids) if factor_ids is not None else None

        def need(fid: str) -> bool:
            return want is None or fid in want

        out: dict[str, pd.DataFrame] = {}
        close = fields.get("close")
        open_ = fields.get("open")
        vol = fields.get("volume")

        if need("alpha101.a1") and close is not None and not close.empty:
            out["alpha101.a1"] = self._alpha1(close)
        if (
            need("alpha101.a3")
            and open_ is not None
            and not open_.empty
            and vol is not None
        ):
            out["alpha101.a3"] = self._alpha3(open_, vol)
        return out

    @staticmethod
    def _alpha1(close: pd.DataFrame) -> pd.DataFrame:
        """Alpha#1 = rank(Ts_ArgMax(SignedPower((ret<0 ? std(ret,20) : close), 2), 5)) - 0.5。

        inner（per-symbol 时序）：收益为负时用 20 日波动否则用收盘，平方后取近 5 根的
        argmax 位置；外层对 inner 做横截面 rank 再减 0.5（→ [-0.5, 0.5]）。
        """
        returns = close.pct_change()
        std20 = returns.rolling(20).std()
        # ret<0 → std20，否则 close（与原式三元一致）
        base = close.where(returns >= 0.0, std20)
        signed_power = np.sign(base) * base.abs() ** 2

        def _ts_argmax5(w: np.ndarray) -> float:
            return np.nan if np.isnan(w).any() else float(np.argmax(w))

        inner = signed_power.apply(
            lambda col: col.rolling(5).apply(_ts_argmax5, raw=True)
        )
        return cross_sectional_rank(inner) - 0.5

    @staticmethod
    def _alpha3(open_: pd.DataFrame, vol: pd.DataFrame) -> pd.DataFrame:
        """Alpha#3 = -1 * correlation(rank(open), rank(volume), 10)。

        先对 open / volume 各做横截面 rank（每期跨标的），再 per-symbol 对两条 rank
        序列算 10 日滚动相关，取负。
        """
        r_open = cross_sectional_rank(open_)
        r_vol = cross_sectional_rank(vol)
        cols: dict[str, pd.Series] = {}
        for sym in r_open.columns:
            if sym in r_vol.columns:
                cols[sym] = r_open[sym].rolling(10).corr(r_vol[sym])
        # 某 10 窗内某标的横截面 rank 恒定（零方差）→ corr 0/0 给 ±inf，归 NaN
        out = -1.0 * pd.DataFrame(cols, index=open_.index)
        return out.replace([np.inf, -np.inf], np.nan)
