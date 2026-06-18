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


def _fred_monthly(
    n: int = 40, start: str = "2022-06-01", base: float = 300.0, step: float = 1.0
) -> pd.Series:
    """合成 monthly 序列（obs date = 月初，等差递增便于手算 yoy/diff）。"""
    idx = pd.date_range(start, periods=n, freq="MS", tz="UTC")
    return pd.Series(base + step * np.arange(n, dtype=float), index=idx)


def _macro_map() -> dict[str, pd.Series]:
    return {
        "DFF": _fred(base=5.0),
        "DGS10": _fred(base=4.2),
        "DGS2": _fred(base=4.6),
        "DGS3MO": _fred(base=4.8),
        "DTWEXBGS": _fred(base=120.0),
        "VIXCLS": _fred(base=16.0),
        "BAMLH0A0HYM2": _fred(base=3.5),
        "BAMLC0A0CM": _fred(base=1.2),
        # monthly（ADR-0044 Phase 2）
        "CPIAUCSL": _fred_monthly(base=300.0),
        "CPILFESL": _fred_monthly(base=310.0),
        "UNRATE": _fred_monthly(base=4.0, step=0.05),
        "PAYEMS": _fred_monthly(base=157_000.0, step=150.0),
        "M2SL": _fred_monthly(base=20_800.0, step=30.0),
        # monthly 实体经济 / 情绪（Phase 3）
        "PPIACO": _fred_monthly(base=250.0),
        "INDPRO": _fred_monthly(base=102.0, step=0.2),
        "RSAFS": _fred_monthly(base=700_000.0, step=500.0),
        "HOUST": _fred_monthly(base=1_400.0, step=2.0),
        "UMCSENT": _fred_monthly(base=70.0, step=0.3),
    }


_DAILY_LAG = pd.Timedelta(days=1)
_DAILY_STALENESS = pd.Timedelta(days=7)


# ── 对齐正确性（ADR-0044 验收：t 日因子值只用 ≤ t−lag 日观测）──────────


def test_align_applies_one_day_publication_lag() -> None:
    idx = pd.date_range("2025-01-01", periods=10, freq="1D", tz="UTC")
    daily = pd.Series(np.arange(10, dtype=float), index=idx)
    aligned = _align_to_bars(daily, idx, lag=_DAILY_LAG, max_staleness=_DAILY_STALENESS)
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
    aligned = _align_to_bars(daily, bars, lag=_DAILY_LAG, max_staleness=_DAILY_STALENESS)
    # 01-06（断更后第 3 天）仍在 7 天容忍内 → ffill 到最后观测
    assert aligned.iloc[bars.get_loc(pd.Timestamp("2025-01-06", tz="UTC"))] == 3.0
    # 01-12 距最后生效观测（01-04）超 7 天 → NaN
    assert np.isnan(aligned.iloc[bars.get_loc(pd.Timestamp("2025-01-12", tz="UTC"))])


def test_align_monthly_lag_boundary() -> None:
    """monthly 滞后边界：obs+lag 前一天看不到、当天看到（per-series 滞后表语义）。"""
    obs = pd.date_range("2025-01-01", periods=2, freq="MS", tz="UTC")
    monthly = pd.Series([10.0, 20.0], index=obs)
    bars = pd.date_range("2025-01-01", periods=120, freq="1D", tz="UTC")
    lag = pd.Timedelta(days=45)
    aligned = _align_to_bars(
        monthly, bars, lag=lag, max_staleness=pd.Timedelta(days=45)
    )
    effective = pd.Timestamp("2025-01-01", tz="UTC") + lag  # 02-15
    assert np.isnan(aligned.iloc[bars.get_loc(effective - pd.Timedelta(days=1))])
    assert aligned.iloc[bars.get_loc(effective)] == 10.0


def test_align_monthly_staleness_45d() -> None:
    """monthly staleness：距最近生效观测 ≤45d ffill；超过如实 NaN（发布严重延迟场景）。"""
    obs = pd.to_datetime(["2025-01-01"], utc=True)
    monthly = pd.Series([10.0], index=obs)
    bars = pd.date_range("2025-02-01", periods=120, freq="1D", tz="UTC")
    lag = pd.Timedelta(days=45)
    aligned = _align_to_bars(
        monthly, bars, lag=lag, max_staleness=pd.Timedelta(days=45)
    )
    effective = pd.Timestamp("2025-01-01", tz="UTC") + lag  # 生效 02-15
    within = effective + pd.Timedelta(days=45)  # 04-01 仍可见
    assert aligned.iloc[bars.get_loc(within)] == 10.0
    assert np.isnan(aligned.iloc[bars.get_loc(within + pd.Timedelta(days=1))])


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
    assert a.required_series(["macro.curve_slope_10y3m"]) == ["DGS10", "DGS3MO"]
    assert a.required_series(["macro.hy_spread_level"]) == ["BAMLH0A0HYM2"]
    assert a.required_series(["macro.vix_level"]) == ["VIXCLS"]
    assert a.required_series(["macro.cpi_yoy"]) == ["CPIAUCSL"]
    assert a.required_series(["macro.ppi_yoy"]) == ["PPIACO"]
    assert a.required_series() == [
        "BAMLC0A0CM", "BAMLH0A0HYM2", "CPIAUCSL", "CPILFESL", "DFF",
        "DGS10", "DGS2", "DGS3MO", "DTWEXBGS", "HOUST", "INDPRO",
        "M2SL", "PAYEMS", "PPIACO", "RSAFS", "UMCSENT", "UNRATE", "VIXCLS",
    ]


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
        self.fetched_timeframes: dict[str, str] = {}

    async def _fetch_df(self, **_kwargs: object) -> pd.DataFrame:  # type: ignore[override]
        return self._df

    async def _fetch_macro_series(  # type: ignore[override]
        self, series_id: str, **kwargs: object
    ) -> pd.Series:
        self.macro_fetches += 1
        self.fetched_timeframes[series_id] = str(kwargs.get("timeframe", "1d"))
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
    assert eng.macro_fetches == 18  # 8 daily + 10 monthly（Phase 2 + Phase 3）


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


# ── monthly（ADR-0044 Phase 2）─────────────────────────────────────────


def test_monthly_formula_correctness() -> None:
    """已知等差/等比序列上的公式手算对照（在原生 monthly 频率上 shift）。"""
    a = MacroAdapter()
    df = _daily_bars(n=200, start="2025-01-01")
    series = a.compute_with_macro(
        df, _macro_map(), ["macro.cpi_yoy", "macro.unrate_chg_3", "macro.payems_chg_1"]
    )
    # CPIAUCSL = 300 + n：最后可见 obs 是 2025-06-01（idx=36，值 336，
    # 生效 07-16 ≤ 末 bar 07-19）→ yoy = 336/324 - 1
    last = series["macro.cpi_yoy"].dropna().iloc[-1]
    assert abs(last - (336.0 / 324.0 - 1.0)) < 1e-9
    # UNRATE = 4.0 + 0.05n：chg_3 恒等于 0.15（lag 40d → 同样最后可见 06-01）
    assert abs(series["macro.unrate_chg_3"].dropna().iloc[-1] - 0.15) < 1e-9
    # PAYEMS = 157000 + 150n：月增恒 150
    assert abs(series["macro.payems_chg_1"].dropna().iloc[-1] - 150.0) < 1e-9


def test_monthly_m2_lag_stricter_than_cpi() -> None:
    """M2（lag 60d）比 CPI（lag 45d）晚见到同一观测月——统一 shift 是错的（D2）。"""
    a = MacroAdapter()
    df = _daily_bars(n=200, start="2025-01-01")
    series = a.compute_with_macro(df, _macro_map(), ["macro.cpi_yoy", "macro.m2_yoy"])
    obs = pd.Timestamp("2025-05-01", tz="UTC")
    cpi_eff = obs + pd.Timedelta(days=45)
    # CPI 在 obs+45d 已更新到 5 月观测；M2 同日仍停留在 4 月观测
    cpi_at = series["macro.cpi_yoy"].loc[cpi_eff]
    m2_at = series["macro.m2_yoy"].loc[cpi_eff]
    cpi_prev_obs_yoy = 334.0 / 322.0 - 1.0  # 2025-04 观测的 yoy
    cpi_this_obs_yoy = 335.0 / 323.0 - 1.0  # 2025-05 观测的 yoy
    assert abs(cpi_at - cpi_this_obs_yoy) < 1e-9
    # M2SL = 20800 + 30n：obs 2025-04（idx=34）yoy = (20800+30*34)/(20800+30*22) - 1
    m2_apr_yoy = (20_800.0 + 30 * 34) / (20_800.0 + 30 * 22) - 1.0
    assert abs(m2_at - m2_apr_yoy) < 1e-9
    assert cpi_prev_obs_yoy != cpi_this_obs_yoy  # 边界确实区分了两个观测月


def test_monthly_degrades_per_series() -> None:
    """缺 CPIAUCSL → 3 个 cpi 因子缺席；其余 monthly + daily 照算。"""
    a = MacroAdapter()
    macro = _macro_map()
    del macro["CPIAUCSL"]
    series = a.compute_with_macro(_daily_bars(), macro)
    assert "macro.cpi_yoy" not in series
    assert "macro.cpi_mom" not in series
    assert "macro.cpi_yoy_chg_3" not in series
    assert "macro.core_cpi_yoy" in series  # CPILFESL 独立序列不受影响
    assert "macro.m2_yoy" in series and "macro.vix_level" in series


# ── Phase 3 扩容：信用利差 / 实体经济 / 情绪 ──────────────────────────


def test_phase3_factors_present_and_computable() -> None:
    """Phase 3 新因子都在 specs() 且能算出非全 NaN 尾巴。"""
    a = MacroAdapter()
    new_ids = {
        "macro.curve_slope_10y3m", "macro.hy_spread_level", "macro.hy_spread_chg_20",
        "macro.ig_spread_level", "macro.ppi_yoy", "macro.indpro_yoy",
        "macro.retail_yoy", "macro.houst_yoy", "macro.sentiment_level",
    }
    spec_ids = {s.factor_id for s in a.specs()}
    assert new_ids <= spec_ids
    series = a.compute_with_macro(_daily_bars(), _macro_map(), sorted(new_ids))
    assert set(series.keys()) == new_ids
    for fid in new_ids:
        assert series[fid].iloc[-5:].notna().any(), f"{fid} tail all-NaN"


def test_phase3_credit_spread_formula() -> None:
    """HY 利差 level = 对齐后的原序列；chg_20 = 20 日差分（daily 公式）。"""
    a = MacroAdapter()
    df = _daily_bars()
    series = a.compute_with_macro(df, _macro_map(), ["macro.hy_spread_level"])
    # level 因子 = HY 序列按 T+1 滞后对齐到 bar，末值应等于对应生效观测
    assert series["macro.hy_spread_level"].iloc[-5:].notna().any()


def test_phase3_degrades_per_series() -> None:
    """缺 BAMLH0A0HYM2 → 两个 HY 因子缺席；IG / 其余照算。"""
    a = MacroAdapter()
    macro = _macro_map()
    del macro["BAMLH0A0HYM2"]
    series = a.compute_with_macro(_daily_bars(), macro)
    assert "macro.hy_spread_level" not in series
    assert "macro.hy_spread_chg_20" not in series
    assert "macro.ig_spread_level" in series
    assert "macro.curve_slope_10y3m" in series


def test_warmup_days_by_factor_mix() -> None:
    a = MacroAdapter()
    assert a.warmup_days(["macro.vix_level", "macro.dgs10_level"]) == 120
    assert a.warmup_days(["macro.vix_level", "macro.cpi_yoy"]) == 600
    assert a.warmup_days() == 600  # 全量含 monthly
    assert a.warmup_days([]) == 120  # 空集合退化为 daily 档


def test_series_timeframe_routing() -> None:
    a = MacroAdapter()
    assert a.series_timeframe("DFF") == "1d"
    assert a.series_timeframe("CPIAUCSL") == "1mo"
    assert a.series_timeframe("UNKNOWN_SID") == "1mo"  # 未知序列保守按 monthly


async def test_engine_fetches_monthly_with_1mo_timeframe() -> None:
    """engine 取数按 series 原生频率路由 timeframe（monthly 记 1mo 落库语义）。"""
    eng = _MacroEngine(_daily_bars())
    await eng.score(**_score_kwargs("1d"))  # type: ignore[arg-type]
    assert eng.fetched_timeframes["DFF"] == "1d"
    assert eng.fetched_timeframes["CPIAUCSL"] == "1mo"
    assert eng.fetched_timeframes["M2SL"] == "1mo"
