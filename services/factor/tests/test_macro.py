"""宏观因子单测（ADR-0044）：滞后对齐 / staleness / timeframe 门 / 优雅降级。"""
from __future__ import annotations

import numpy as np
import pandas as pd

from inalpha_factor.adapters import MacroAdapter
from inalpha_factor.adapters.macro_adapter import _align_to_bars
from inalpha_factor.config import get_factor_settings
from inalpha_factor.engine import FactorEngine


def _daily_bars(n: int = 200, start: str = "2025-01-01") -> pd.DataFrame:
    rng = np.random.default_rng(5)
    rets = rng.normal(0.0003, 0.015, size=n)
    close = 100.0 * np.exp(np.cumsum(rets))
    idx = pd.date_range(start, periods=n, freq="1D", tz="UTC")
    return pd.DataFrame(
        {
            "open": close, "high": close * 1.005, "low": close * 0.995,
            "close": close, "volume": rng.uniform(800, 1200, size=n),
        },
        index=idx,
    )


def _fred(n: int = 320, start: str = "2024-09-01", base: float = 4.0) -> pd.Series:
    rng = np.random.default_rng(8)
    idx = pd.date_range(start, periods=n, freq="1D", tz="UTC")
    return pd.Series(base + np.cumsum(rng.normal(0, 0.02, size=n)), index=idx)


def _macro_map() -> dict[str, pd.Series]:
    return {
        "DFF": _fred(base=5.0),
        "DGS10": _fred(base=4.2),
        "DGS2": _fred(base=4.6),
        "DTWEXBGS": _fred(base=120.0),
        "VIXCLS": _fred(base=16.0),
    }


# ── 对齐正确性（ADR-0044 验收：t 日因子值只用 ≤ t−1 日观测）──────────


def test_align_applies_one_day_publication_lag() -> None:
    idx = pd.date_range("2025-01-01", periods=10, freq="1D", tz="UTC")
    daily = pd.Series(np.arange(10, dtype=float), index=idx)
    aligned = _align_to_bars(daily, idx)
    # t 日的 bar 只能看到 t-1 日观测：值整体滞后一位，首日无可用观测
    assert np.isnan(aligned.iloc[0])
    for i in range(1, 10):
        assert aligned.iloc[i] == float(i - 1), f"bar {i} 看到了未来观测"


def test_align_ffill_weekend_but_caps_staleness() -> None:
    """周末/假日 ffill 合法；断更超 7 天必须 NaN（不冒充最新）。"""
    obs = pd.to_datetime(
        ["2025-01-01", "2025-01-02", "2025-01-03"], utc=True
    )  # 之后断更
    daily = pd.Series([1.0, 2.0, 3.0], index=obs)
    bars = pd.date_range("2025-01-02", periods=20, freq="1D", tz="UTC")
    aligned = _align_to_bars(daily, bars)
    # 01-06（断更后第 3 天）仍在 7 天容忍内 → ffill 到最后观测
    assert aligned.iloc[bars.get_loc(pd.Timestamp("2025-01-06", tz="UTC"))] == 3.0
    # 01-12 距最后生效观测（01-04）超 7 天 → NaN
    assert np.isnan(aligned.iloc[bars.get_loc(pd.Timestamp("2025-01-12", tz="UTC"))])


# ── adapter 计算 ─────────────────────────────────────────────────────


def test_compute_with_macro_all_factors() -> None:
    a = MacroAdapter()
    df = _daily_bars()
    series = a.compute_with_macro(df, _macro_map())
    assert set(series.keys()) == {s.factor_id for s in a.specs()}
    for fid, s in series.items():
        assert s.index.equals(df.index)
        assert s.iloc[-5:].notna().any(), f"{fid} tail all-NaN"


def test_compute_with_macro_degrades_per_series() -> None:
    """缺 DGS2 → 曲线因子缺席，其余照算。"""
    a = MacroAdapter()
    macro = _macro_map()
    del macro["DGS2"]
    series = a.compute_with_macro(_daily_bars(), macro)
    assert "macro.curve_slope" not in series
    assert "macro.curve_slope_chg_20" not in series
    assert "macro.dgs10_level" in series and "macro.vix_level" in series


def test_required_series_mapping() -> None:
    a = MacroAdapter()
    assert a.required_series(["macro.curve_slope"]) == ["DGS10", "DGS2"]
    assert a.required_series(["macro.vix_level"]) == ["VIXCLS"]
    assert a.required_series() == ["DFF", "DGS10", "DGS2", "DTWEXBGS", "VIXCLS"]


def test_protocol_compute_returns_empty() -> None:
    """价量路径算不了宏观因子：协议 compute 恒返空（engine 走 compute_with_macro）。"""
    assert MacroAdapter().compute(_daily_bars()) == {}


# ── engine 集成：timeframe 门 + 降级 ─────────────────────────────────


class _MacroEngine(FactorEngine):
    def __init__(self, df: pd.DataFrame, *, fail_macro: bool = False) -> None:
        super().__init__(get_factor_settings())
        self._df = df
        self._fail_macro = fail_macro
        self.macro_fetches = 0

    async def _fetch_df(self, **_kwargs: object) -> pd.DataFrame:  # type: ignore[override]
        return self._df

    async def _fetch_macro_series(  # type: ignore[override]
        self, series_id: str, **_kwargs: object
    ) -> pd.Series:
        self.macro_fetches += 1
        if self._fail_macro:
            raise ValueError("no FRED key")
        return _macro_map()[series_id]


def _score_kwargs(timeframe: str) -> dict[str, object]:
    return {
        "venue": "binance", "symbol": "BTC/USDT", "timeframe": timeframe,
        "as_of": None, "lookback_bars": 150, "horizon_bars": 5,
        "quantiles": 5, "factor_ids": None,
    }


async def test_macro_in_daily_score() -> None:
    eng = _MacroEngine(_daily_bars())
    res = await eng.score(**_score_kwargs("1d"))  # type: ignore[arg-type]
    ids = {f["factor_id"] for f in res["factors"]}
    assert any(fid.startswith("macro.") for fid in ids)
    assert eng.macro_fetches == 5


async def test_macro_skipped_intraday() -> None:
    """1h 请求：宏观因子不进候选、不发起任何 FRED 取数、不报错（D3）。"""
    eng = _MacroEngine(_daily_bars())
    res = await eng.score(**_score_kwargs("1h"))  # type: ignore[arg-type]
    ids = {f["factor_id"] for f in res["factors"]}
    assert not any(fid.startswith("macro.") for fid in ids)
    assert eng.macro_fetches == 0


async def test_macro_fetch_failure_degrades_gracefully() -> None:
    """FRED 拉不到（key 缺失等）：价量因子照常返回，无异常（验收条款）。"""
    eng = _MacroEngine(_daily_bars(), fail_macro=True)
    res = await eng.score(**_score_kwargs("1d"))  # type: ignore[arg-type]
    ids = {f["factor_id"] for f in res["factors"]}
    assert not any(fid.startswith("macro.") for fid in ids)
    assert any(fid.startswith("qlib.") for fid in ids)  # 价量不受影响
