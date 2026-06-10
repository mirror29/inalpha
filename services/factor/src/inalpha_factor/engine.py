"""因子引擎 —— 串起 data 取数 / 适配器算因子 / 有效性打分。

各 API 路由的业务核心都在这里，路由只做 schema 适配。
"""
from __future__ import annotations

import logging
import time
from collections import OrderedDict
from datetime import UTC, datetime, timedelta
from typing import Any

import numpy as np
import pandas as pd

from .adapters import Alpha101Adapter, FactorAdapter, FactorSpec, PandasTAAdapter, QlibAlphaAdapter
from .config import FactorSettings
from .data_client import DataClient
from .effectiveness import EffResult, score_factor

logger = logging.getLogger(__name__)

# 不同 timeframe 估算每根 bar 的秒数，用于把 lookback_bars 换算成时间窗口拉数据。
_TF_SECONDS: dict[str, int] = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "2h": 7200,
    "4h": 14400,
    "1d": 86400,
    "1wk": 604800,
    "1w": 604800,
}


def _tf_seconds(timeframe: str) -> int:
    return _TF_SECONDS.get(timeframe, 3600)


# ── 因子面板缓存（live 热路径）────────────────────────────────────────
# engine 每请求新建（deps.get_engine），缓存放模块级。只缓存 is_live 调用——
# agent timing 短时间内对同一标的连问是常态，每次重拉 bar + 重算 50 因子纯浪费。
# 金融时效性守门：实际 TTL = min(FACTOR_CACHE_TTL_S, 半根 bar)，最多半根 bar stale；
# 历史 as_of 不走缓存（低频且 key 难收敛）；空 df 不入缓存（data 抖一下别毒 5 分钟）。
_PANEL_CACHE_MAX = 64
_PanelEntry = tuple[float, pd.DataFrame, dict[str, pd.Series]]
_panel_cache: OrderedDict[tuple[Any, ...], _PanelEntry] = OrderedDict()


def _panel_cache_get(
    key: tuple[Any, ...], ttl_s: float
) -> tuple[pd.DataFrame, dict[str, pd.Series]] | None:
    entry = _panel_cache.get(key)
    if entry is None:
        return None
    ts, df, series = entry
    if time.monotonic() - ts > ttl_s:
        _panel_cache.pop(key, None)
        return None
    _panel_cache.move_to_end(key)
    return df, series


def _panel_cache_put(
    key: tuple[Any, ...], df: pd.DataFrame, series: dict[str, pd.Series]
) -> None:
    _panel_cache[key] = (time.monotonic(), df, series)
    _panel_cache.move_to_end(key)
    while len(_panel_cache) > _PANEL_CACHE_MAX:
        _panel_cache.popitem(last=False)


class FactorEngine:
    """因子计算 + 有效性打分。无状态（每次请求新建 DataClient）。"""

    def __init__(self, settings: FactorSettings, token: str = "") -> None:
        self._settings = settings
        self._token = token
        self._adapters: list[FactorAdapter] = [
            PandasTAAdapter(),
            Alpha101Adapter(),
            QlibAlphaAdapter(enabled=settings.qlib_enabled),
        ]

    # ── catalog ──────────────────────────────────────────────────────
    def sources(self) -> dict[str, bool]:
        return {a.source: a.available() for a in self._adapters}

    def catalog(self) -> list[FactorSpec]:
        specs: list[FactorSpec] = []
        for a in self._adapters:
            avail = a.available()
            for s in a.specs():
                # qlib 关闭时把 available 透传到 spec（catalog 仍露出，标未启用）
                specs.append(s if avail else _mark_unavailable(s))
        return specs

    def _spec_index(self) -> dict[str, FactorSpec]:
        return {s.factor_id: s for s in self.catalog()}

    def _computable_ids(self) -> list[str]:
        """所有可时序计算（非横截面、源可用）的因子 id。"""
        ids: list[str] = []
        for a in self._adapters:
            if not a.available():
                continue
            for s in a.specs():
                if not s.needs_universe:
                    ids.append(s.factor_id)
        return ids

    # ── 取数 ─────────────────────────────────────────────────────────
    async def _fetch_df(
        self,
        *,
        venue: str,
        symbol: str,
        timeframe: str,
        from_ts: datetime,
        to_ts: datetime,
        fresh: bool = False,
    ) -> pd.DataFrame:
        async with DataClient(self._settings.data_service_url, self._token) as dc:
            bars = await dc.get_bars(
                venue=venue, symbol=symbol, timeframe=timeframe,
                from_ts=from_ts, to_ts=to_ts, fresh=fresh,
            )
        return bars_to_df(bars)

    # ── compute ──────────────────────────────────────────────────────
    async def compute_series(
        self,
        *,
        venue: str,
        symbol: str,
        timeframe: str,
        from_ts: datetime,
        to_ts: datetime,
        factor_ids: list[str] | None,
    ) -> tuple[pd.DataFrame, dict[str, pd.Series]]:
        df = await self._fetch_df(
            venue=venue, symbol=symbol, timeframe=timeframe, from_ts=from_ts, to_ts=to_ts
        )
        series = self.compute_on_df(df, factor_ids)
        return df, series

    def compute_on_df(
        self, df: pd.DataFrame, factor_ids: list[str] | None
    ) -> dict[str, pd.Series]:
        out: dict[str, pd.Series] = {}
        if df.empty:
            return out
        for a in self._adapters:
            if not a.available():
                continue
            try:
                out.update(a.compute(df, factor_ids))
            except Exception as exc:  # 单个源算挂不影响其他源，但要可观测（ADR-0043 D5）
                logger.warning("factor adapter %s compute failed: %r", a.source, exc)
                continue
        return out

    # ── score ────────────────────────────────────────────────────────
    async def score(
        self,
        *,
        venue: str,
        symbol: str,
        timeframe: str,
        as_of: datetime | None,
        lookback_bars: int,
        horizon_bars: int,
        quantiles: int,
        factor_ids: list[str] | None,
    ) -> dict[str, Any]:
        scored, _series = await self._score_with_series(
            venue=venue,
            symbol=symbol,
            timeframe=timeframe,
            as_of=as_of,
            lookback_bars=lookback_bars,
            horizon_bars=horizon_bars,
            quantiles=quantiles,
            factor_ids=factor_ids,
        )
        return scored

    async def _score_with_series(
        self,
        *,
        venue: str,
        symbol: str,
        timeframe: str,
        as_of: datetime | None,
        lookback_bars: int,
        horizon_bars: int,
        quantiles: int,
        factor_ids: list[str] | None,
    ) -> tuple[dict[str, Any], dict[str, pd.Series]]:
        """score 主体；额外返回因子时序，给 snapshot 去相关用（ADR-0043 D3）。"""
        # "现在"做择时（factor.timing/snapshot，含 research analyst 传 as_of=deep_dive 的当前
        # 时刻）→ 必须 fresh（先 backfill 到现在再算），否则尾巴 stale 让"当前因子方向"是几小时
        # 前的状态（§3.1）。判 live 不能只看 as_of is None —— 调用方常显式传"当前时刻"；as_of 落在
        # 最近 ~2 根 bar 内即视为 live。显式给较早 as_of（历史分析）才 fresh=False（不补未来）。
        now = datetime.now(UTC)
        is_live = as_of is None or as_of >= now - timedelta(seconds=_tf_seconds(timeframe) * 2)
        as_of = as_of or now
        # live 调用走面板缓存：TTL 上限半根 bar，stale 风险有界（§3.1）
        ttl_s = min(float(self._settings.cache_ttl_s), _tf_seconds(timeframe) / 2)
        ids_key = tuple(sorted(factor_ids)) if factor_ids else "*"
        cache_key = (venue, symbol, timeframe, lookback_bars, horizon_bars, ids_key)
        cacheable = is_live and ttl_s > 0
        cached = _panel_cache_get(cache_key, ttl_s) if cacheable else None
        if cached is not None:
            df, series = cached
            ids = factor_ids or self._computable_ids()
        else:
            # 多拉 horizon + 60 根 warmup，保证有效性样本充足
            span_bars = lookback_bars + horizon_bars + 60
            from_ts = as_of - timedelta(seconds=_tf_seconds(timeframe) * span_bars)
            df = await self._fetch_df(
                venue=venue, symbol=symbol, timeframe=timeframe,
                from_ts=from_ts, to_ts=as_of, fresh=is_live,
            )
            # 只用 <= as_of 的 bar（防未来数据）；空 df 的 index 是 RangeIndex，跳过比较
            if not df.empty and isinstance(df.index, pd.DatetimeIndex):
                df = df[df.index <= pd.Timestamp(as_of)]
            ids = factor_ids or self._computable_ids()
            series = self.compute_on_df(df, ids)
            if cacheable and not df.empty:
                _panel_cache_put(cache_key, df, series)
        specs = self._spec_index()
        results: list[dict[str, Any]] = []
        close = df["close"].astype(float) if not df.empty else pd.Series(dtype=float)
        for fid in ids:
            s = series.get(fid)
            spec = specs.get(fid)
            if s is None or spec is None or close.empty:
                continue
            eff = score_factor(
                s,
                close,
                horizon=horizon_bars,
                quantiles=quantiles,
                min_samples=self._settings.min_effective_samples,
            )
            results.append(_eff_to_dict(spec, eff))
        return {
            "as_of": as_of,
            "bars_used": len(df),
            "factors": results,
        }, series

    async def snapshot(
        self,
        *,
        venue: str,
        symbol: str,
        timeframe: str,
        as_of: datetime | None,
        lookback_bars: int,
        horizon_bars: int,
        top_n: int | None,
    ) -> dict[str, Any]:
        scored, series = await self._score_with_series(
            venue=venue,
            symbol=symbol,
            timeframe=timeframe,
            as_of=as_of,
            lookback_bars=lookback_bars,
            horizon_bars=horizon_bars,
            quantiles=5,
            factor_ids=None,
        )
        factors: list[dict[str, Any]] = scored["factors"]
        # 只保留有信心的，按 |rank_ic| 降序、去相关后取 top-N（ADR-0043 D3）
        confident = [f for f in factors if not f["low_confidence"]]
        confident.sort(key=lambda f: abs(f["rank_ic"]), reverse=True)
        n = top_n or self._settings.snapshot_top_n
        top = _select_decorrelated(
            confident, series, n, self._settings.snapshot_corr_threshold
        )
        return {
            "as_of": scored["as_of"],
            "bars_used": scored["bars_used"],
            "available": scored["bars_used"] > 0 and len(factors) > 0,
            "reason": None if scored["bars_used"] > 0 else "no bars from data-service",
            "top_factors": top,
            "candidates_evaluated": len(factors),
            "low_confidence_count": len(factors) - len(confident),
        }


def bars_to_df(bars: list[dict[str, Any]]) -> pd.DataFrame:
    """data-service BarResponse 列表 → OHLCV DataFrame（index = tz-aware ts，升序）。"""
    if not bars:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    df = pd.DataFrame(bars)
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    df = df.set_index("ts").sort_index()
    cols = ["open", "high", "low", "close", "volume"]
    for c in cols:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df[cols]


def _mark_unavailable(spec: FactorSpec) -> FactorSpec:
    return FactorSpec(
        factor_id=spec.factor_id,
        source=spec.source,
        name=spec.name,
        kind=spec.kind,
        needs_universe=spec.needs_universe,
        direction_hint=spec.direction_hint,
        extras={**spec.extras, "available": "false"},
    )


def _select_decorrelated(
    ranked: list[dict[str, Any]],
    series: dict[str, pd.Series],
    n: int,
    threshold: float,
) -> list[dict[str, Any]]:
    """贪心去相关：按 |rank_ic| 降序遍历，与已选因子时序 |spearman| ≥ threshold 则跳过。

    被挤掉的因子 id 记进胜者的 ``corr_pruned``，让 agent 知道该信号有多少同质替身。
    重叠样本 < 30 时不判相关（信息不足，宁可放行）。
    """
    selected: list[dict[str, Any]] = []
    for cand in ranked:
        if len(selected) >= n:
            break
        cand_series = series.get(cand["factor_id"])
        winner: dict[str, Any] | None = None
        for sel in selected:
            corr = _abs_spearman(cand_series, series.get(sel["factor_id"]))
            if corr is not None and corr >= threshold:
                winner = sel
                break
        if winner is not None:
            winner["corr_pruned"].append(cand["factor_id"])
        else:
            cand["corr_pruned"] = []
            selected.append(cand)
    return selected


def _abs_spearman(a: pd.Series | None, b: pd.Series | None) -> float | None:
    """两条因子时序的 |spearman|；样本不足 / 常数列返回 None（视作不可判）。"""
    if a is None or b is None:
        return None
    pair = pd.concat([a, b], axis=1).replace([np.inf, -np.inf], np.nan).dropna()
    if len(pair) < 30:
        return None
    ar = pair.iloc[:, 0].rank()
    br = pair.iloc[:, 1].rank()
    if ar.std(ddof=0) == 0 or br.std(ddof=0) == 0:
        return None
    c = ar.corr(br)
    return None if np.isnan(c) else abs(float(c))


def _eff_to_dict(spec: FactorSpec, eff: EffResult) -> dict[str, Any]:
    return {
        "factor_id": spec.factor_id,
        "source": spec.source,
        "name": spec.name,
        "kind": spec.kind,
        "value": eff.value,
        "rank_ic": eff.rank_ic,
        "rank_ic_recent": eff.rank_ic_recent,
        "icir": eff.icir,
        "turnover": eff.turnover,
        "sample_size": eff.sample_size,
        "quantile_returns": [
            {"q": q, "mean_return": m, "sample_size": n} for (q, m, n) in eff.quantile_returns
        ],
        "long_short_return": eff.long_short_return,
        "direction": eff.direction,
        "strength": eff.strength,
        "low_confidence": eff.low_confidence,
    }
