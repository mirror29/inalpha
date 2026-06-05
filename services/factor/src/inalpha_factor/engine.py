"""因子引擎 —— 串起 data 取数 / 适配器算因子 / 有效性打分。

各 API 路由的业务核心都在这里，路由只做 schema 适配。
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pandas as pd

from .adapters import Alpha101Adapter, FactorAdapter, FactorSpec, PandasTAAdapter, QlibAlphaAdapter
from .config import FactorSettings
from .data_client import DataClient
from .effectiveness import EffResult, score_factor

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
            except Exception:  # 单个源算挂不影响其他源
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
        # "现在"做择时（factor.timing/snapshot，含 research analyst 传 as_of=deep_dive 的当前
        # 时刻）→ 必须 fresh（先 backfill 到现在再算），否则尾巴 stale 让"当前因子方向"是几小时
        # 前的状态（§3.1）。判 live 不能只看 as_of is None —— 调用方常显式传"当前时刻"；as_of 落在
        # 最近 ~2 根 bar 内即视为 live。显式给较早 as_of（历史分析）才 fresh=False（不补未来）。
        now = datetime.now(UTC)
        is_live = as_of is None or as_of >= now - timedelta(seconds=_tf_seconds(timeframe) * 2)
        as_of = as_of or now
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
        }

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
        scored = await self.score(
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
        # 只保留有信心的，按 |rank_ic| 降序取 top-N
        confident = [f for f in factors if not f["low_confidence"]]
        confident.sort(key=lambda f: abs(f["rank_ic"]), reverse=True)
        n = top_n or self._settings.snapshot_top_n
        top = confident[:n]
        return {
            "as_of": scored["as_of"],
            "bars_used": scored["bars_used"],
            "available": scored["bars_used"] > 0 and len(factors) > 0,
            "reason": None if scored["bars_used"] > 0 else "no bars from data-service",
            "top_factors": top,
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


def _eff_to_dict(spec: FactorSpec, eff: EffResult) -> dict[str, Any]:
    return {
        "factor_id": spec.factor_id,
        "source": spec.source,
        "name": spec.name,
        "kind": spec.kind,
        "value": eff.value,
        "rank_ic": eff.rank_ic,
        "icir": eff.icir,
        "sample_size": eff.sample_size,
        "quantile_returns": [
            {"q": q, "mean_return": m, "sample_size": n} for (q, m, n) in eff.quantile_returns
        ],
        "long_short_return": eff.long_short_return,
        "direction": eff.direction,
        "strength": eff.strength,
        "low_confidence": eff.low_confidence,
    }
