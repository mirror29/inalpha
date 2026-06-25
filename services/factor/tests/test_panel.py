"""横截面 Panel 单测：对齐 / 横截面 IC / 选标的排名 / engine 集成。"""
from __future__ import annotations

import numpy as np
import pandas as pd

from inalpha_factor.adapters import Alpha101Adapter
from inalpha_factor.config import get_factor_settings
from inalpha_factor.engine import FactorEngine
from inalpha_factor.panel import (
    align_field,
    cross_sectional_ic,
    cross_sectional_rank,
    forward_return_panel,
    latest_cross_section,
)

_SYMS = ["A", "B", "C", "D", "E"]


def _panel(rows: list[list[float]]) -> pd.DataFrame:
    idx = pd.date_range("2025-01-01", periods=len(rows), freq="1D", tz="UTC")
    return pd.DataFrame(rows, index=idx, columns=_SYMS)


def _bars(n: int = 220, seed: int = 1, start: str = "2025-01-01") -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100.0 * np.exp(np.cumsum(rng.normal(0.0004, 0.02, size=n)))
    idx = pd.date_range(start, periods=n, freq="1D", tz="UTC")
    return pd.DataFrame(
        {
            "open": close * (1 + rng.normal(0, 0.001, n)),
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "volume": rng.uniform(800, 1200, n),
        },
        index=idx,
    )


# ── align_field ──────────────────────────────────────────────────────


def test_align_field_union_index_no_ffill() -> None:
    """不同标的索引并集对齐；缺口留 NaN，不前向填充。"""
    a = pd.DataFrame(
        {"close": [1.0, 2.0, 3.0]},
        index=pd.date_range("2025-01-01", periods=3, freq="1D", tz="UTC"),
    )
    b = pd.DataFrame(
        {"close": [10.0, 20.0]},
        index=pd.date_range("2025-01-02", periods=2, freq="1D", tz="UTC"),
    )
    panel = align_field({"A": a, "B": b}, "close")
    assert list(panel.columns) == ["A", "B"]
    assert len(panel) == 3  # 01-01..01-03 并集
    # B 在 01-01 没有观测 → NaN（不被 ffill）
    assert np.isnan(panel.iloc[0]["B"])
    assert panel.iloc[1]["B"] == 10.0


def test_align_field_skips_empty() -> None:
    panel = align_field({"A": _bars(20), "B": pd.DataFrame()}, "close")
    assert list(panel.columns) == ["A"]


# ── forward_return_panel ─────────────────────────────────────────────


def test_forward_return_panel_no_future_leak() -> None:
    close = _panel([[10.0] * 5, [11.0] * 5, [12.0] * 5])
    fwd = forward_return_panel(close, horizon=1)
    assert abs(fwd.iloc[0]["A"] - (11.0 / 10.0 - 1.0)) < 1e-12
    assert fwd.iloc[-1].isna().all()  # 末 H 行无前瞻收益


# ── cross_sectional_ic ───────────────────────────────────────────────


def test_cross_sectional_ic_perfect_positive() -> None:
    """因子横截面序与前瞻收益横截面序完全同序 → 每期 spearman=1 → mean_ic≈1。"""
    factor = _panel([[1.0, 2.0, 3.0, 4.0, 5.0]] * 6)
    fwd = _panel([[0.1, 0.2, 0.3, 0.4, 0.5]] * 6)
    mean_ic, _icir, n_periods, mean_valid = cross_sectional_ic(factor, fwd, min_symbols=3)
    assert abs(mean_ic - 1.0) < 1e-9
    assert n_periods == 6
    assert mean_valid == 5.0


def test_cross_sectional_ic_perfect_negative() -> None:
    factor = _panel([[1.0, 2.0, 3.0, 4.0, 5.0]] * 4)
    fwd = _panel([[0.5, 0.4, 0.3, 0.2, 0.1]] * 4)
    mean_ic, _icir, _n, _v = cross_sectional_ic(factor, fwd, min_symbols=3)
    assert abs(mean_ic + 1.0) < 1e-9


def test_cross_sectional_ic_min_symbols_gate() -> None:
    """有效标的不足 min_symbols 的期被跳过（残缺池不排名，D1.1）。"""
    factor = _panel(
        [[1.0, 2.0, np.nan, np.nan, np.nan], [1.0, 2.0, 3.0, 4.0, 5.0]]
    )
    fwd = _panel([[0.1, 0.2, 0.3, 0.4, 0.5], [0.1, 0.2, 0.3, 0.4, 0.5]])
    _ic, _icir, n_periods, _v = cross_sectional_ic(factor, fwd, min_symbols=3)
    assert n_periods == 1  # 第一期只有 2 个有效标的 → 跳过


def test_cross_sectional_ic_empty() -> None:
    mean_ic, icir, n_periods, mean_valid = cross_sectional_ic(
        pd.DataFrame(), pd.DataFrame(), min_symbols=3
    )
    assert (mean_ic, icir, n_periods, mean_valid) == (0.0, 0.0, 0, 0.0)


# ── latest_cross_section（选标的）─────────────────────────────────────


def test_latest_cross_section_sorted_ascending() -> None:
    """返回最近有效横截面的升序排名——取最低=首（如聚宽 PB 最低）。"""
    factor = _panel([[3.0, 1.0, 2.0, 5.0, 4.0]])
    t, ranking = latest_cross_section(factor, min_symbols=3)
    assert t is not None
    assert [sym for sym, _v, _r in ranking] == ["B", "C", "A", "E", "D"]
    assert ranking[0][1] == 1.0  # 最低值
    assert ranking[-1][2] == 1.0  # 最高值的 rank_pct = 1.0


def test_latest_cross_section_skips_incomplete_tail() -> None:
    """末期有效标的不足时回退到更早的足量横截面。"""
    factor = _panel(
        [[1.0, 2.0, 3.0, 4.0, 5.0], [9.0, np.nan, np.nan, np.nan, np.nan]]
    )
    _t, ranking = latest_cross_section(factor, min_symbols=3)
    assert len(ranking) == 5  # 用了第一行,不是残缺的末行


# ── cross_sectional_rank（内禀横截面基础算子）────────────────────────


def test_cross_sectional_rank_handcheck() -> None:
    """每行横截面百分位 rank；NaN 不参与、保持 NaN（手算对照）。

    行 [10,30,20,nan,nan]：3 个有效值 → rank/3。10→1/3、20→2/3、30→3/3；NaN 保持。
    """
    panel = _panel([[10.0, 30.0, 20.0, np.nan, np.nan]])
    ranked = cross_sectional_rank(panel).iloc[0]
    assert abs(ranked["A"] - 1.0 / 3.0) < 1e-12  # 10 最低
    assert abs(ranked["C"] - 2.0 / 3.0) < 1e-12  # 20 居中
    assert abs(ranked["B"] - 1.0) < 1e-12  # 30 最高
    assert np.isnan(ranked["D"]) and np.isnan(ranked["E"])


def test_cross_sectional_rank_three_values() -> None:
    panel = _panel([[10.0, 30.0, 20.0, 40.0, 50.0]])
    ranked = cross_sectional_rank(panel).iloc[0]
    # 5 个值升序 rank/5：10→.2 20→.4 30→.6 40→.8 50→1.0
    assert abs(ranked["A"] - 0.2) < 1e-12
    assert abs(ranked["C"] - 0.4) < 1e-12
    assert abs(ranked["B"] - 0.6) < 1e-12
    assert abs(ranked["E"] - 1.0) < 1e-12


# ── 内禀横截面 alpha101.a1 / a3──────────────────────


def test_alpha1_intrinsic_cross_sectional() -> None:
    """a1 = rank(Ts_ArgMax(...))-0.5 ∈ [-0.5, 0.5]，横截面（依赖全池）。"""
    a = Alpha101Adapter()
    frames = {s: _bars(seed=i + 1) for i, s in enumerate(_SYMS)}
    fields = {f: align_field(frames, f) for f in ("open", "close", "volume")}
    out = a.compute_cross_sectional(fields, ["alpha101.a1"])
    a1 = out["alpha101.a1"]
    assert list(a1.columns) == _SYMS
    vals = a1.to_numpy()
    finite = vals[~np.isnan(vals)]
    assert finite.size > 0
    assert finite.min() >= -0.5 - 1e-9 and finite.max() <= 0.5 + 1e-9
    assert a1.iloc[-5:].notna().any().any()  # 尾部有值


def test_alpha3_intrinsic_cross_sectional() -> None:
    """a3 = -corr(rank(open), rank(volume), 10) ∈ [-1, 1]。"""
    a = Alpha101Adapter()
    frames = {s: _bars(seed=i + 10) for i, s in enumerate(_SYMS)}
    fields = {f: align_field(frames, f) for f in ("open", "close", "volume")}
    out = a.compute_cross_sectional(fields, ["alpha101.a3"])
    a3 = out["alpha101.a3"]
    assert list(a3.columns) == _SYMS
    vals = a3.to_numpy()
    finite = vals[~np.isnan(vals)]
    assert finite.size > 0
    assert finite.min() >= -1.0 - 1e-9 and finite.max() <= 1.0 + 1e-9


def test_compute_cross_sectional_filters_ids() -> None:
    a = Alpha101Adapter()
    frames = {s: _bars(seed=i + 1) for i, s in enumerate(_SYMS)}
    fields = {f: align_field(frames, f) for f in ("open", "close", "volume")}
    out = a.compute_cross_sectional(fields, ["alpha101.a1"])
    assert set(out) == {"alpha101.a1"}  # 没点 a3 就不算


# ── engine.panel_score 集成 ──────────────────────────────────────────


class _PanelEngine(FactorEngine):
    def __init__(self, frames: dict[str, pd.DataFrame]) -> None:
        super().__init__(get_factor_settings())
        self._frames = frames

    async def _fetch_df(self, *, symbol: str, **_kw: object) -> pd.DataFrame:  # type: ignore[override]
        return self._frames.get(symbol, pd.DataFrame())


def _kwargs(symbols: list[str], **over: object) -> dict[str, object]:
    base: dict[str, object] = {
        "symbols": symbols, "venue": "binance", "timeframe": "1d",
        "as_of": None, "lookback_bars": 150, "horizon_bars": 5,
        "factor_ids": None, "min_symbols": 3,
    }
    base.update(over)
    return base


async def test_panel_score_end_to_end() -> None:
    frames = {s: _bars(seed=i + 1) for i, s in enumerate(_SYMS)}
    eng = _PanelEngine(frames)
    res = await eng.panel_score(**_kwargs(_SYMS))  # type: ignore[arg-type]
    assert res["is_pit"] is False  # 非 PIT 显式标注
    assert res["factors"], "应有横截面因子结果"
    assert set(res["bars_used"]) == set(_SYMS)
    for f in res["factors"]:
        assert f["ic_kind"] == "cross_sectional"
        assert isinstance(f["cross_sectional_ic"], float)
        assert 0 < len(f["latest_ranking"]) <= len(_SYMS)
    # macro 不参与横截面（全市场单值无区分度）
    assert not any(f["factor_id"].startswith("macro.") for f in res["factors"])
    # 内禀横截面 alpha（needs_universe）已原生算入
    fids = {f["factor_id"] for f in res["factors"]}
    assert "alpha101.a1" in fids and "alpha101.a3" in fids


class _FlakyPanelEngine(_PanelEngine):
    """指定标的的 _fetch_df 抛错，模拟单标的临时 HTTP 失败。"""

    def __init__(self, frames: dict[str, pd.DataFrame], fail: str) -> None:
        super().__init__(frames)
        self._fail = fail

    async def _fetch_df(self, *, symbol: str, **_kw: object) -> pd.DataFrame:  # type: ignore[override]
        if symbol == self._fail:
            raise RuntimeError("simulated data-service error")
        return await super()._fetch_df(symbol=symbol, **_kw)


async def test_panel_score_degrades_on_single_symbol_fetch_failure() -> None:
    """一个标的 fetch 抛错 → 降级为空，其余标的照常出横截面结果（不整体 500）。"""
    frames = {s: _bars(seed=i + 1) for i, s in enumerate(_SYMS)}
    eng = _FlakyPanelEngine(frames, fail="C")
    res = await eng.panel_score(**_kwargs(_SYMS))  # type: ignore[arg-type]
    assert res["factors"], "其余标的应仍产出横截面因子"
    assert res["bars_used"]["C"] == 0  # 失败标的记 0 bar
    assert all(res["bars_used"][s] > 0 for s in _SYMS if s != "C")


async def test_panel_score_below_min_symbols_empty() -> None:
    """universe 标的数 < min_symbols → 无因子可横截面评估,显式 reason。"""
    frames = {"A": _bars(seed=1), "B": _bars(seed=2)}
    eng = _PanelEngine(frames)
    res = await eng.panel_score(**_kwargs(["A", "B"], min_symbols=3))  # type: ignore[arg-type]
    assert res["factors"] == []
