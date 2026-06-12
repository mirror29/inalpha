"""宏观因子适配器（FRED 序列 → 因子，ADR-0044 Phase 1 daily + Phase 2 monthly）。

因子库第一类**外生信息源**：利率 / 期限利差 / 美元指数 / VIX（daily）+ 通胀 /
就业 / 货币（monthly）。数据走 data-service 的 ``venue="fred"``（值在 bar 的
``close`` 字段），**engine 负责取数**（D1），本 adapter 只做纯计算：吃
"FRED series id → 序列"的 dict，吐对齐到标的 bar index 的因子时序。

Point-in-time（D2/D3，§3.1 红线）：

- **per-series 静态发布滞后表**（``_SERIES_META``）：daily 市场化序列 +1 天；
  monthly 统计序列从 observation date（参考月 1 日）起算 = 月长 + 发布延迟——
  CPI（BLS 次月中旬）45d / 就业（次月首个周五）40d / M2（H.6 次月第 4 个周二，
  最晚 ~59d）60d。**统一 shift 是错的**：取 45d 对 M2 是 lookahead bug，取 60d
  让 CPI/就业每月白扔两周合法信息。ALFRED vintage / revision 仍不做（已知局限）。
- 对齐用 ``merge_asof(direction="backward")``，**绝不向前看**；staleness 上限
  按频率分档：daily 7 天（覆盖周末/假日）、monthly 45 天（覆盖月度节奏 + 发布
  抖动；发布延迟超 2 周如实 NaN，不冒充最新）
- 仅 1d / 1wk timeframe 计算（engine 守门）：intraday ffill 会造 rank-tie 伪样本；
  monthly 因子在 1d bar 上是 ~30 bar 一变的阶梯函数（sample_size 会高估独立观测
  数），spec extras 带 ``freq`` 透出让 caller 心里有数

协议说明：``compute(df)`` 恒返 {}（宏观因子需要第二份数据，价量路径算不了），
engine 走 ``compute_with_macro``。
"""
from __future__ import annotations

import logging
from collections.abc import Callable

import numpy as np
import pandas as pd

from .base import FactorSpec

logger = logging.getLogger(__name__)

#: 宏观因子允许的 timeframe（engine 守门用）
MACRO_TIMEFRAMES: frozenset[str] = frozenset({"1d", "1wk", "1w"})

#: FRED series → (原生频率, 保守发布滞后天数)。
#: 滞后从 observation date 起算；monthly 的 obs date 是参考月 1 日。
_SERIES_META: dict[str, tuple[str, int]] = {
    # daily 市场化序列（Phase 1）：T+1 发布
    "DFF": ("d", 1),
    "DGS10": ("d", 1),
    "DGS2": ("d", 1),
    "DTWEXBGS": ("d", 1),
    "VIXCLS": ("d", 1),
    # monthly 统计序列（Phase 2）：月长 + 发布延迟
    "CPIAUCSL": ("m", 45),
    "CPILFESL": ("m", 45),
    "UNRATE": ("m", 40),
    "PAYEMS": ("m", 40),
    "M2SL": ("m", 60),
}

#: 未知序列的兜底滞后（保守取 monthly 最大档）
_FALLBACK_LAG_DAYS = 60

#: staleness 上限按频率分档：距最近**生效**观测超过该值 → NaN
_MAX_STALENESS_BY_FREQ: dict[str, pd.Timedelta] = {
    "d": pd.Timedelta(days=7),
    "m": pd.Timedelta(days=45),
}

#: warmup 天数：daily 公式最深 chg_60 + 余量；monthly YoY 动量需 15 个月观测 + 60d 滞后
_WARMUP_DAYS_DAILY = 120
_WARMUP_DAYS_MONTHLY = 600

_TF_NOTE = "1d,1wk"


def _spec(factor_id: str, name: str, fred: str, *, freq: str = "daily") -> FactorSpec:
    return FactorSpec(
        factor_id,
        "macro",
        name,
        "macro",
        extras={"fred": fred, "timeframes": _TF_NOTE, "freq": freq},
    )


_SPECS: list[FactorSpec] = [
    _spec("macro.dff_chg_20", "联邦基金利率 20 日变化", "DFF"),
    _spec("macro.dgs10_level", "10Y 美债收益率水平", "DGS10"),
    _spec("macro.dgs10_chg_20", "10Y 美债收益率 20 日变化", "DGS10"),
    _spec("macro.curve_slope", "期限利差 10Y-2Y", "DGS10,DGS2"),
    _spec("macro.curve_slope_chg_20", "期限利差 20 日变化", "DGS10,DGS2"),
    _spec("macro.dollar_roc_20", "广义美元指数 20 日动量", "DTWEXBGS"),
    _spec("macro.dollar_roc_60", "广义美元指数 60 日动量", "DTWEXBGS"),
    _spec("macro.vix_level", "VIX 水平", "VIXCLS"),
    _spec("macro.vix_chg_20", "VIX 20 日变化", "VIXCLS"),
    # ── monthly（ADR-0044 Phase 2）。刻意不做二阶衍生（m2_yoy_chg_3 等），
    #    控制候选数（ADR-0043 多重检验纪律）──
    _spec("macro.cpi_yoy", "CPI 同比", "CPIAUCSL", freq="monthly"),
    _spec("macro.cpi_mom", "CPI 环比", "CPIAUCSL", freq="monthly"),
    _spec("macro.cpi_yoy_chg_3", "CPI 同比 3 月动量", "CPIAUCSL", freq="monthly"),
    _spec("macro.core_cpi_yoy", "核心 CPI 同比", "CPILFESL", freq="monthly"),
    _spec("macro.unrate_level", "失业率水平", "UNRATE", freq="monthly"),
    _spec("macro.unrate_chg_3", "失业率 3 月变动", "UNRATE", freq="monthly"),
    _spec("macro.payems_chg_1", "非农就业月增（千人）", "PAYEMS", freq="monthly"),
    _spec("macro.m2_yoy", "M2 同比增速", "M2SL", freq="monthly"),
]

_SPEC_BY_ID: dict[str, FactorSpec] = {s.factor_id: s for s in _SPECS}


def _series_meta(sid: str) -> tuple[str, int]:
    return _SERIES_META.get(sid, ("m", _FALLBACK_LAG_DAYS))


def _factor_align_params(fid: str) -> tuple[pd.Timedelta, pd.Timedelta]:
    """因子的对齐参数 ``(lag, max_staleness)`` = 其依赖序列里最保守的一档。"""
    spec = _SPEC_BY_ID[fid]
    sids = spec.extras["fred"].split(",")
    lag_days = max(_series_meta(sid)[1] for sid in sids)
    freqs = {_series_meta(sid)[0] for sid in sids}
    staleness = max(
        (_MAX_STALENESS_BY_FREQ[f] for f in freqs),
        default=_MAX_STALENESS_BY_FREQ["m"],
    )
    return pd.Timedelta(days=lag_days), staleness


class MacroAdapter:
    """FRED 宏观因子源（engine 取数，本类纯计算）。"""

    source = "macro"

    def __init__(self, enabled: bool = True) -> None:
        self._enabled = enabled

    def available(self) -> bool:
        return self._enabled

    def specs(self) -> list[FactorSpec]:
        return _SPECS

    def compute(
        self, df: pd.DataFrame, factor_ids: list[str] | None = None
    ) -> dict[str, pd.Series]:
        # 宏观因子需要 FRED 序列（engine._compute_macro 取数后走 compute_with_macro）；
        # 纯价量路径无从算起，恒返空。
        return {}

    def required_series(self, factor_ids: list[str] | None = None) -> list[str]:
        """请求的因子需要哪些 FRED series id（engine 据此取数）。"""
        want = set(factor_ids) if factor_ids is not None else None
        out: set[str] = set()
        for s in _SPECS:
            if want is None or s.factor_id in want:
                out.update(s.extras["fred"].split(","))
        return sorted(out)

    def series_timeframe(self, series_id: str) -> str:
        """series 的取数 timeframe（engine 调 data ``/bars`` 用）：daily→1d，monthly→1mo。

        monthly 序列必须按 ``1mo`` 落库：data 侧 backfill 的 span 估算与 bars 表
        ``(ts, venue, symbol, timeframe)`` key 都按 timeframe 语义工作，monthly 观测
        记成 ``1d`` 会让 backfill cursor 空转。
        """
        freq, _ = _series_meta(series_id)
        return "1mo" if freq == "m" else "1d"

    def warmup_days(self, factor_ids: list[str] | None = None) -> int:
        """取数窗口要往前多拉多少天（engine ``_compute_macro`` 用）。

        daily 公式最深 chg_60 → 120 天足够；含任一 monthly 因子时 YoY 动量需
        12+3 个月观测 + 60d 发布滞后 ≈ 520 天 → 取 600（FRED monthly 600 天
        ≈ 20 个观测，多拉开销可忽略）。老调用方不传 monthly 因子时行为不变。
        """
        want = set(factor_ids) if factor_ids is not None else None
        for s in _SPECS:
            if want is not None and s.factor_id not in want:
                continue
            if s.extras.get("freq") == "monthly":
                return _WARMUP_DAYS_MONTHLY
        return _WARMUP_DAYS_DAILY

    def compute_with_macro(
        self,
        df: pd.DataFrame,
        macro: dict[str, pd.Series],
        factor_ids: list[str] | None = None,
    ) -> dict[str, pd.Series]:
        """从 FRED daily 序列算因子并对齐到标的 bar index。

        Args:
            df: 标的 OHLCV（index = tz-aware ts 升序），只用其 index。
            macro: FRED series id → daily 序列（tz-aware index，值 = close）。
                缺某 series 时依赖它的因子跳过（优雅降级）。
            factor_ids: 只算这些；None = 全部。
        """
        if df.empty:
            return {}
        want = set(factor_ids) if factor_ids is not None else None

        def need(fid: str) -> bool:
            return want is None or fid in want

        def get(sid: str) -> pd.Series | None:
            s = macro.get(sid)
            if s is None:
                return None
            s = s.replace([np.inf, -np.inf], np.nan).dropna().sort_index()
            return s if len(s) else None

        dff, dgs10, dgs2 = get("DFF"), get("DGS10"), get("DGS2")
        dxy, vix = get("DTWEXBGS"), get("VIXCLS")
        slope = (dgs10 - dgs2).dropna() if dgs10 is not None and dgs2 is not None else None
        cpi, core_cpi = get("CPIAUCSL"), get("CPILFESL")
        unrate, payems, m2 = get("UNRATE"), get("PAYEMS"), get("M2SL")

        def _yoy(s: pd.Series) -> pd.Series:
            # shift 按观测数计：monthly 原生序列上 shift(12) = 12 个月
            return s / s.shift(12).replace(0.0, np.nan) - 1.0

        # 原生频率上先算因子（daily 的 shift=日，monthly 的 shift=月），再按
        # per-series 滞后表统一做生效日对齐
        formulas: dict[str, Callable[[], pd.Series | None]] = {
            "macro.dff_chg_20": lambda: dff - dff.shift(20) if dff is not None else None,
            "macro.dgs10_level": lambda: dgs10,
            "macro.dgs10_chg_20": (
                lambda: dgs10 - dgs10.shift(20) if dgs10 is not None else None
            ),
            "macro.curve_slope": lambda: slope,
            "macro.curve_slope_chg_20": (
                lambda: slope - slope.shift(20) if slope is not None else None
            ),
            "macro.dollar_roc_20": (
                lambda: dxy / dxy.shift(20).replace(0.0, np.nan) - 1.0
                if dxy is not None
                else None
            ),
            "macro.dollar_roc_60": (
                lambda: dxy / dxy.shift(60).replace(0.0, np.nan) - 1.0
                if dxy is not None
                else None
            ),
            "macro.vix_level": lambda: vix,
            "macro.vix_chg_20": lambda: vix - vix.shift(20) if vix is not None else None,
            # monthly（ADR-0044 Phase 2）
            "macro.cpi_yoy": lambda: _yoy(cpi) if cpi is not None else None,
            "macro.cpi_mom": (
                lambda: cpi / cpi.shift(1).replace(0.0, np.nan) - 1.0
                if cpi is not None
                else None
            ),
            "macro.cpi_yoy_chg_3": (
                lambda: _yoy(cpi) - _yoy(cpi).shift(3) if cpi is not None else None
            ),
            "macro.core_cpi_yoy": lambda: _yoy(core_cpi) if core_cpi is not None else None,
            "macro.unrate_level": lambda: unrate,
            "macro.unrate_chg_3": (
                lambda: unrate - unrate.shift(3) if unrate is not None else None
            ),
            "macro.payems_chg_1": lambda: payems.diff(1) if payems is not None else None,
            "macro.m2_yoy": lambda: _yoy(m2) if m2 is not None else None,
        }

        out: dict[str, pd.Series] = {}
        for fid, fn in formulas.items():
            if not need(fid):
                continue
            # 单因子隔离：formulas 与 _SPECS 漏同步会让 _factor_align_params 抛
            # KeyError，若不隔离会被 engine 的 except 吞掉、整批宏观因子一起静默消失
            # 且无归因。逐 fid try/except，坏一条只丢一条并记下具体 fid。
            try:
                native = fn()
                if native is None:
                    continue
                lag, staleness = _factor_align_params(fid)
                out[fid] = _align_to_bars(
                    native, df.index, lag=lag, max_staleness=staleness
                )
            except Exception:
                logger.warning("macro factor %s 计算失败（已跳过）", fid, exc_info=True)
        return out


def _align_to_bars(
    native: pd.Series,
    bar_index: pd.Index,
    *,
    lag: pd.Timedelta,
    max_staleness: pd.Timedelta,
) -> pd.Series:
    """原生频率因子值 → 标的 bar index：发布滞后 + asof 向后取 + staleness 上限。

    t 时刻的 bar 只能看到 observation date ≤ t - lag 的宏观观测（D2，per-series
    滞后表见 ``_SERIES_META``）；距最近**生效**观测 > max_staleness 留 NaN
    （D3"不冒充最新"——daily 7d 覆盖周末/假日，monthly 45d 覆盖月度节奏 +
    发布抖动，序列断更/发布严重延迟时如实缺数）。
    """
    s = native.replace([np.inf, -np.inf], np.nan).dropna().sort_index()
    if s.empty or len(bar_index) == 0:
        return pd.Series(np.nan, index=bar_index)
    effective = s.copy()
    effective.index = effective.index + lag
    merged = pd.merge_asof(
        pd.DataFrame({"ts": bar_index}),
        effective.rename("v").rename_axis("ts").reset_index(),
        on="ts",
        direction="backward",
        tolerance=max_staleness,
    )
    return pd.Series(merged["v"].to_numpy(), index=bar_index)
