"""三源适配器单测：输出 shape + 末值有限 + qlib 降级。"""
from __future__ import annotations

import numpy as np
import pandas as pd

from inalpha_factor.adapters import Alpha101Adapter, PandasTAAdapter, QlibAlphaAdapter


def _finite_tail(s: pd.Series, tail: int = 5) -> bool:
    """末 tail 个值至少有一个有限（非全 NaN/inf）——warmup 之后应算得出。"""
    vals = s.replace([np.inf, -np.inf], np.nan).iloc[-tail:]
    return bool(vals.notna().any())


def test_pandas_ta_core_always_available(ohlcv: pd.DataFrame) -> None:
    a = PandasTAAdapter()
    assert a.available() is True
    series = a.compute(ohlcv)
    # 12 个核心因子必出
    for fid in [
        "pandas_ta.rsi_14",
        "pandas_ta.macd_hist",
        "pandas_ta.atr_pct_14",
        "pandas_ta.bb_pctb_20",
        "pandas_ta.adx_14",
        "pandas_ta.sma_ratio_20_50",
    ]:
        assert fid in series, f"missing {fid}"
        assert _finite_tail(series[fid]), f"{fid} tail all-NaN"


def test_pandas_ta_filter_factor_ids(ohlcv: pd.DataFrame) -> None:
    a = PandasTAAdapter()
    series = a.compute(ohlcv, ["pandas_ta.rsi_14"])
    assert set(series.keys()) == {"pandas_ta.rsi_14"}


def test_rsi_in_range(ohlcv: pd.DataFrame) -> None:
    a = PandasTAAdapter()
    rsi = a.compute(ohlcv, ["pandas_ta.rsi_14"])["pandas_ta.rsi_14"].dropna()
    assert (rsi >= 0).all() and (rsi <= 100).all()


def test_alpha101_timeseries_subset(ohlcv: pd.DataFrame) -> None:
    a = Alpha101Adapter()
    assert a.available() is True
    series = a.compute(ohlcv)
    assert "alpha101.a101" in series
    assert _finite_tail(series["alpha101.a101"])
    # 横截面项不应被计算
    assert "alpha101.a1" not in series
    # 但出现在 catalog 里且标 needs_universe
    specs = {s.factor_id: s for s in a.specs()}
    assert specs["alpha101.a1"].needs_universe is True


def test_qlib_disabled_returns_empty(ohlcv: pd.DataFrame) -> None:
    a = QlibAlphaAdapter(enabled=False)
    assert a.available() is False
    assert a.compute(ohlcv) == {}
    # catalog 仍列出定义（便于前端知道存在）
    assert len(a.specs()) > 0


def test_qlib_pure_pandas_always_computes(ohlcv: pd.DataFrame) -> None:
    """ADR-0043 D1：纯 pandas 实现，不依赖 pyqlib，启用即可算全部因子。"""
    a = QlibAlphaAdapter()
    assert a.available() is True
    series = a.compute(ohlcv)
    spec_ids = {s.factor_id for s in a.specs()}
    assert len(spec_ids) >= 30
    # spec 与实现一一对应（漏实现/漏 spec 都挂）
    assert set(series.keys()) == spec_ids
    for fid, s in series.items():
        assert _finite_tail(s), f"{fid} tail all-NaN"


def test_qlib_bounded_factors_in_range(ohlcv: pd.DataFrame) -> None:
    """RSV/CNTP/CNTN/SUMP 数学上落在 [0,1]。"""
    a = QlibAlphaAdapter()
    series = a.compute(
        ohlcv, ["qlib.rsv_20", "qlib.cntp_20", "qlib.cntn_20", "qlib.sump_20"]
    )
    for fid, s in series.items():
        vals = s.dropna()
        assert ((vals >= 0) & (vals <= 1)).all(), f"{fid} out of [0,1]"


def test_qlib_filter_factor_ids(ohlcv: pd.DataFrame) -> None:
    a = QlibAlphaAdapter()
    series = a.compute(ohlcv, ["qlib.roc_20"])
    assert set(series.keys()) == {"qlib.roc_20"}
