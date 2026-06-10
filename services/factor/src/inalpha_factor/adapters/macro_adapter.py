"""宏观因子适配器（FRED daily 序列 → 因子，ADR-0044）。

因子库第一类**外生信息源**：利率 / 期限利差 / 美元指数 / VIX。数据走 data-service
的 ``venue="fred"``（值在 bar 的 ``close`` 字段），**engine 负责取数**（D1），本
adapter 只做纯计算：吃"FRED series id → daily 序列"的 dict，吐对齐到标的 bar
index 的因子时序。

Point-in-time（D2/D3，§3.1 红线）：

- Phase 1 只用 daily 市场化序列（发布滞后 ~1 天）→ 统一 **+1 天 shift** 保守对齐，
  不需要发布日历；monthly 序列（CPI/UNRATE/M2）等 Phase 2
- 对齐用 ``merge_asof(direction="backward")``，**绝不向前看**；距最近可用观测
  超过 ``_MAX_STALENESS`` 直接 NaN（周末/假日 ffill 合法，序列断更不冒充）
- 仅 1d / 1wk timeframe 计算（engine 守门）：intraday ffill 会造 rank-tie 伪样本

协议说明：``compute(df)`` 恒返 {}（宏观因子需要第二份数据，价量路径算不了），
engine 走 ``compute_with_macro``。
"""
from __future__ import annotations

from collections.abc import Callable

import numpy as np
import pandas as pd

from .base import FactorSpec

#: 宏观因子允许的 timeframe（engine 守门用）
MACRO_TIMEFRAMES: frozenset[str] = frozenset({"1d", "1wk", "1w"})

#: 发布滞后：daily 市场化序列统一保守 +1 天
_PUBLICATION_LAG = pd.Timedelta(days=1)

#: 距最近可用观测超过该值 → NaN（覆盖周末/假日，挡住序列断更）
_MAX_STALENESS = pd.Timedelta(days=7)

_TF_NOTE = "1d,1wk"


def _spec(factor_id: str, name: str, fred: str) -> FactorSpec:
    return FactorSpec(
        factor_id,
        "macro",
        name,
        "macro",
        extras={"fred": fred, "timeframes": _TF_NOTE},
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
]


class MacroAdapter:
    """FRED daily 宏观因子源（engine 取数，本类纯计算）。"""

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

        # daily 频率上先算因子，再统一做滞后对齐
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
        }

        out: dict[str, pd.Series] = {}
        for fid, fn in formulas.items():
            if not need(fid):
                continue
            daily = fn()
            if daily is None:
                continue
            out[fid] = _align_to_bars(daily, df.index)
        return out


def _align_to_bars(daily: pd.Series, bar_index: pd.Index) -> pd.Series:
    """daily 因子值 → 标的 bar index：+1 天发布滞后 + asof 向后取 + staleness 上限。

    t 时刻的 bar 只能看到 observation date ≤ t-1 天的宏观观测（D2）；
    距最近可用观测 > _MAX_STALENESS 留 NaN（D3"不冒充最新"）。
    """
    s = daily.replace([np.inf, -np.inf], np.nan).dropna().sort_index()
    if s.empty or len(bar_index) == 0:
        return pd.Series(np.nan, index=bar_index)
    effective = s.copy()
    effective.index = effective.index + _PUBLICATION_LAG
    merged = pd.merge_asof(
        pd.DataFrame({"ts": bar_index}),
        effective.rename("v").rename_axis("ts").reset_index(),
        on="ts",
        direction="backward",
        tolerance=_MAX_STALENESS,
    )
    return pd.Series(merged["v"].to_numpy(), index=bar_index)
