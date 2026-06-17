"""pandas-ta 技术指标适配器。

设计取舍（见 docs/miro/11 §5）：pandas-ta 历史上对 numpy 2.x 兼容脆弱
（``from numpy import NaN`` 在 numpy≥2 报错）。为保证 factor 服务在任何机器上都能
产出真因子（M2 timing / M3 analyst 永不空手），**核心 ~12 个指标用纯 pandas/numpy
自算**（``_USE_LIB`` 无关，永远可用）；**pandas-ta 库装上时额外补几个库特有指标**
（WILLR / MFI / CMF），真正用到这个现成库。

核心指标都做了 scale-free 归一（除以 close / 用比值），方便跨标的与跨周期比较有效性。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .base import FactorSpec

try:  # 库装上时补充库特有指标；装不上不影响核心
    import pandas_ta as _pta

    _HAS_PTA = True
except Exception:  # pragma: no cover - 取决于环境
    _HAS_PTA = False


def _rma(s: pd.Series, length: int) -> pd.Series:
    """Wilder RMA（== ewm(alpha=1/length)）。"""
    return s.ewm(alpha=1.0 / length, adjust=False).mean()


def _true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr


# 核心因子定义（纯 pandas，永远可用）──────────────────────────────────
_CORE: list[FactorSpec] = [
    FactorSpec("pandas_ta.rsi_14", "pandas_ta", "RSI(14)", "mean_reversion", direction_hint=-1),
    FactorSpec("pandas_ta.macd_hist", "pandas_ta", "MACD hist(12,26,9)/close", "momentum", direction_hint=1),
    FactorSpec("pandas_ta.atr_pct_14", "pandas_ta", "ATR(14)/close", "volatility"),
    FactorSpec("pandas_ta.bb_pctb_20", "pandas_ta", "Bollinger %B(20,2)", "mean_reversion", direction_hint=-1),
    FactorSpec("pandas_ta.adx_14", "pandas_ta", "ADX(14) trend strength", "trend"),
    FactorSpec("pandas_ta.stoch_k_14", "pandas_ta", "Stochastic %K(14)", "mean_reversion", direction_hint=-1),
    FactorSpec("pandas_ta.roc_10", "pandas_ta", "ROC(10)", "momentum", direction_hint=1),
    FactorSpec("pandas_ta.sma_ratio_20_50", "pandas_ta", "SMA20/SMA50-1", "trend", direction_hint=1),
    FactorSpec("pandas_ta.cci_20", "pandas_ta", "CCI(20)", "mean_reversion", direction_hint=-1),
    FactorSpec("pandas_ta.mom_20", "pandas_ta", "Momentum(20) close return", "momentum", direction_hint=1),
    FactorSpec("pandas_ta.vol_ratio_20", "pandas_ta", "Volume ratio volume/MA20", "volume", direction_hint=1),
    FactorSpec("pandas_ta.obv_mom_20", "pandas_ta", "OBV 20-period momentum", "volume", direction_hint=1),
]

# 库特有的额外指标（仅 pandas_ta 装上时出现）
_LIB_EXTRA: list[FactorSpec] = [
    FactorSpec("pandas_ta.willr_14", "pandas_ta", "Williams %R(14)", "mean_reversion", direction_hint=1),
    FactorSpec("pandas_ta.mfi_14", "pandas_ta", "Money Flow Index(14)", "volume", direction_hint=-1),
    FactorSpec("pandas_ta.cmf_20", "pandas_ta", "Chaikin Money Flow(20)", "volume", direction_hint=1),
]


class PandasTAAdapter:
    """pandas-ta 技术指标源。核心永远可用，库装上时多几个因子。"""

    source = "pandas_ta"

    def available(self) -> bool:
        # 核心纯 pandas 实现永远可用，所以本源永远 available（与 qlib 不同）。
        return True

    def uses_library(self) -> bool:
        return _HAS_PTA

    def specs(self) -> list[FactorSpec]:
        specs = list(_CORE)
        if _HAS_PTA:
            specs += _LIB_EXTRA
        return specs

    def compute(
        self, df: pd.DataFrame, factor_ids: list[str] | None = None
    ) -> dict[str, pd.Series]:
        want = set(factor_ids) if factor_ids is not None else None

        def need(fid: str) -> bool:
            return want is None or fid in want

        out: dict[str, pd.Series] = {}
        close = df["close"].astype(float)
        high = df["high"].astype(float)
        low = df["low"].astype(float)
        volume = df["volume"].astype(float)

        if need("pandas_ta.rsi_14"):
            delta = close.diff()
            gain = delta.clip(lower=0.0)
            loss = (-delta).clip(lower=0.0)
            rs = _rma(gain, 14) / _rma(loss, 14).replace(0.0, np.nan)
            out["pandas_ta.rsi_14"] = 100.0 - 100.0 / (1.0 + rs)

        if need("pandas_ta.macd_hist"):
            ema_fast = close.ewm(span=12, adjust=False).mean()
            ema_slow = close.ewm(span=26, adjust=False).mean()
            macd = ema_fast - ema_slow
            signal = macd.ewm(span=9, adjust=False).mean()
            out["pandas_ta.macd_hist"] = (macd - signal) / close.replace(0.0, np.nan)

        if need("pandas_ta.atr_pct_14"):
            atr = _rma(_true_range(df), 14)
            out["pandas_ta.atr_pct_14"] = atr / close.replace(0.0, np.nan)

        if need("pandas_ta.bb_pctb_20"):
            ma = close.rolling(20).mean()
            sd = close.rolling(20).std()
            upper = ma + 2.0 * sd
            lower = ma - 2.0 * sd
            width = (upper - lower).replace(0.0, np.nan)
            out["pandas_ta.bb_pctb_20"] = (close - lower) / width

        if need("pandas_ta.adx_14"):
            out["pandas_ta.adx_14"] = self._adx(df, 14)

        if need("pandas_ta.stoch_k_14"):
            ll = low.rolling(14).min()
            hh = high.rolling(14).max()
            rng = (hh - ll).replace(0.0, np.nan)
            out["pandas_ta.stoch_k_14"] = 100.0 * (close - ll) / rng

        if need("pandas_ta.roc_10"):
            out["pandas_ta.roc_10"] = close.pct_change(10) * 100.0

        if need("pandas_ta.sma_ratio_20_50"):
            sma20 = close.rolling(20).mean()
            sma50 = close.rolling(50).mean()
            out["pandas_ta.sma_ratio_20_50"] = sma20 / sma50.replace(0.0, np.nan) - 1.0

        if need("pandas_ta.cci_20"):
            tp = (high + low + close) / 3.0
            ma = tp.rolling(20).mean()
            md = (tp - ma).abs().rolling(20).mean().replace(0.0, np.nan)
            out["pandas_ta.cci_20"] = (tp - ma) / (0.015 * md)

        if need("pandas_ta.mom_20"):
            out["pandas_ta.mom_20"] = close.pct_change(20)

        if need("pandas_ta.vol_ratio_20"):
            out["pandas_ta.vol_ratio_20"] = volume / volume.rolling(20).mean().replace(0.0, np.nan)

        if need("pandas_ta.obv_mom_20"):
            obv = (np.sign(close.diff().fillna(0.0)) * volume).cumsum()
            denom = volume.rolling(20).sum().replace(0.0, np.nan)
            out["pandas_ta.obv_mom_20"] = obv.diff(20) / denom

        if _HAS_PTA:
            out.update(self._lib_extra(df, need))

        return out

    def _adx(self, df: pd.DataFrame, length: int) -> pd.Series:
        high = df["high"].astype(float)
        low = df["low"].astype(float)
        up = high.diff()
        down = -low.diff()
        plus_dm = pd.Series(np.where((up > down) & (up > 0.0), up, 0.0), index=df.index)
        minus_dm = pd.Series(np.where((down > up) & (down > 0.0), down, 0.0), index=df.index)
        atr = _rma(_true_range(df), length).replace(0.0, np.nan)
        plus_di = 100.0 * _rma(plus_dm, length) / atr
        minus_di = 100.0 * _rma(minus_dm, length) / atr
        di_sum = (plus_di + minus_di).replace(0.0, np.nan)
        dx = 100.0 * (plus_di - minus_di).abs() / di_sum
        return _rma(dx, length)

    def _lib_extra(self, df: pd.DataFrame, need) -> dict[str, pd.Series]:  # type: ignore[no-untyped-def]
        """pandas-ta 库特有指标（WILLR / MFI / CMF）。库装上才走这里。"""
        out: dict[str, pd.Series] = {}
        try:
            if need("pandas_ta.willr_14"):
                out["pandas_ta.willr_14"] = _pta.willr(df["high"], df["low"], df["close"], length=14)
            if need("pandas_ta.mfi_14"):
                out["pandas_ta.mfi_14"] = _pta.mfi(
                    df["high"], df["low"], df["close"], df["volume"], length=14
                )
            if need("pandas_ta.cmf_20"):
                out["pandas_ta.cmf_20"] = _pta.cmf(
                    df["high"], df["low"], df["close"], df["volume"], length=20
                )
        except Exception:  # pragma: no cover - 库内部偶发；不影响核心
            pass
        # 统一成 float Series（pandas_ta 偶尔返回 DataFrame / None）
        return {k: pd.Series(v, index=df.index).astype(float) for k, v in out.items() if v is not None}
