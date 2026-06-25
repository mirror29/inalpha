"""因子引擎 —— 串起 data 取数 / 适配器算因子 / 有效性打分。

各 API 路由的业务核心都在这里，路由只做 schema 适配。
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from collections import OrderedDict
from datetime import UTC, datetime, timedelta
from typing import Any

import numpy as np
import pandas as pd

from .adapters import (
    Alpha101Adapter,
    CustomAdapter,
    FactorAdapter,
    FactorSpec,
    MacroAdapter,
    PandasTAAdapter,
    QlibAlphaAdapter,
)
from .adapters.macro_adapter import MACRO_TIMEFRAMES
from .config import FactorSettings
from .data_client import DataClient
from .effectiveness import EffResult, ic_pvalue, null_ic_benchmark, score_factor
from .expression import evaluate, parse_expression
from .panel import (
    MIN_XS_PERIODS,
    align_field,
    cross_sectional_ic,
    forward_return_panel,
    latest_cross_section,
)

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
# 坑(单租户假设):key 不含用户/账户标识 —— 因子面板是公共行情衍生数据,当前可共享;
# 若未来缓存内容沾上用户私有维度(自定义因子/私有数据源),必须把 token 加进 key,
# 否则 A 的面板会返给 B。
_PANEL_CACHE_MAX = 64
# FRED daily 序列一天才更新一次，live 缓存 TTL 放宽到 1 小时（ADR-0044 D1）
_MACRO_CACHE_TTL_S = 3600.0
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
        self._macro = MacroAdapter(enabled=settings.macro_enabled)
        self._adapters: list[FactorAdapter] = [
            PandasTAAdapter(),
            Alpha101Adapter(),
            QlibAlphaAdapter(enabled=settings.qlib_enabled),
            self._macro,
            # D-12 · 因子发现 L1：registered 自定义表达式（注册表为空 = 无因子）
            CustomAdapter(),
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

    def _computable_ids(
        self, timeframe: str | None = None, *, exclude_macro: bool = False
    ) -> list[str]:
        """所有可时序计算（非横截面、源可用）的因子 id。

        宏观因子只在 1d/1wk timeframe 进入（ADR-0044 D3：intraday ffill 会造
        rank-tie 伪样本，IC 虚高）；timeframe=None 表示不过滤（catalog 视角）。

        exclude_macro=True：无论 timeframe 一律排除 macro 源——给 custom_score 的
        冗余对比用（macro 需另拉 FRED 且与价量天然低相关）。别再借 timeframe="1h"
        当"非 macro"代理，否则 1d/1wk 自定义因子会连同 daily 价量因子一起漏掉。
        """
        macro_ok = not exclude_macro and (
            timeframe is None or timeframe in MACRO_TIMEFRAMES
        )
        ids: list[str] = []
        for a in self._adapters:
            if not a.available():
                continue
            if a.source == "macro" and not macro_ok:
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
        ids = factor_ids or self._computable_ids(timeframe)
        series = self.compute_on_df(df, ids)
        series.update(
            await self._compute_macro(
                df, timeframe=timeframe, factor_ids=ids, as_of=to_ts, fresh=False
            )
        )
        return df, series

    # ── macro（ADR-0044）─────────────────────────────────────────────
    async def _compute_macro(
        self,
        df: pd.DataFrame,
        *,
        timeframe: str,
        factor_ids: list[str],
        as_of: datetime,
        fresh: bool,
    ) -> dict[str, pd.Series]:
        """拉 FRED 序列并算宏观因子。任何一环失败只降级（少这批因子），不破坏价量结果。

        降级必须**显式留痕**(§3.1 拿不到时不静默):请求里点名了 macro.* 却返 {}
        时,每条路径都有带原因的日志,排查"宏观因子怎么没了"不用猜。
        """
        want = [fid for fid in factor_ids if fid.startswith("macro.")]
        if not want:
            return {}
        if timeframe not in MACRO_TIMEFRAMES:
            logger.info(
                "macro factors skipped: timeframe %s not in %s (macro 仅日/周频)",
                timeframe, sorted(MACRO_TIMEFRAMES),
            )
            return {}
        if not self._macro.available():
            logger.info("macro factors skipped: macro source disabled (FACTOR_MACRO_ENABLED)")
            return {}
        if df.empty:
            logger.info("macro factors skipped: no price bars to align against")
            return {}
        # warmup 窗口按请求的因子定（ADR-0044 Phase 2）：纯 daily 120 天（chg_60+余量），
        # 含 monthly 600 天（YoY 动量 15 个月观测 + 60d 发布滞后）。旧的硬编码 120 对
        # monthly YoY 是 bug 级缺口——窗口不够长公式全 NaN。
        from_ts = (
            df.index[0].to_pydatetime() if isinstance(df.index, pd.DatetimeIndex) else as_of
        ) - timedelta(days=self._macro.warmup_days(want))
        # 并发拉所有 FRED 序列：1d snapshot 含 18 个宏观序列，串行 await（每个 backfill
        # ~6s）会累积到 ~100s+ 越过客户端超时；并发后墙钟 ≈ 最慢单条。单条失败只丢该条
        # （优雅降级，FRED key 缺失 / data 无 fred venue 仍不破坏价量结果）。
        sids = self._macro.required_series(want)

        async def _fetch_one(sid: str) -> tuple[str, pd.Series | None]:
            try:
                return sid, await self._fetch_macro_series(
                    sid,
                    from_ts=from_ts,
                    to_ts=as_of,
                    fresh=fresh,
                    timeframe=self._macro.series_timeframe(sid),
                )
            except Exception as exc:  # FRED key 缺失 / data 无 fred venue → 优雅降级
                logger.warning("macro series %s fetch failed: %r", sid, exc)
                return sid, None

        fetched = await asyncio.gather(*[_fetch_one(sid) for sid in sids])
        macro: dict[str, pd.Series] = {sid: s for sid, s in fetched if s is not None}
        if not macro:
            logger.info(
                "macro factors degraded: 0/%d FRED series fetched, %d macro factor(s) dropped",
                len(sids), len(want),
            )
            return {}
        try:
            return self._macro.compute_with_macro(df, macro, want)
        except Exception as exc:
            logger.warning("macro adapter compute failed: %r", exc)
            return {}

    async def _fetch_macro_series(
        self,
        series_id: str,
        *,
        from_ts: datetime,
        to_ts: datetime,
        fresh: bool,
        timeframe: str = "1d",
    ) -> pd.Series:
        """单条 FRED 序列（venue="fred"，值在 close；monthly 序列 timeframe="1mo"）。
        live 走专属缓存（FRED 序列至多一天一变，TTL 放宽到 1 小时，不占面板缓存的
        半根 bar 约束）。

        ⚠️ ``fresh`` 语义与 DataClient 相反,别按字面反转条件:这里 fresh=True
        = live 调用(**启用**缓存,TTL 内直接命中);fresh=False = 历史 as_of
        请求(**跳过**缓存——历史 key 难收敛,缓存只会污染)。

        坑:缓存 key 含 to_ts.date() —— 对 T+1 发布的 daily 序列正确;对静态滞后
        ≥ 发布延迟的 monthly 序列同样正确(新观测的生效日 = obs+lag 落在发布日
        之后,发布当天缓存 stale 1 小时也进不了当日因子)。警告只针对**日内更新**
        的 macro 源(如 VIX spot)——加那种源必须改用面板缓存的半根 bar TTL,
        否则当日内会静默返回 stale 值。
        """
        key = (
            "__macro__",
            series_id,
            from_ts.date().isoformat(),
            to_ts.date().isoformat(),
        )
        if fresh:
            cached = _panel_cache_get(key, _MACRO_CACHE_TTL_S)
            if cached is not None:
                # cached = (df, series);macro 条目 series 恒存 {},df 才有 close。
                # 取错下标会 KeyError → 被 _compute_macro 的兜底吃掉,宏观因子
                # 在缓存命中后全部静默消失(review #70 round2 major)。
                return cached[0]["close"]
        df = await self._fetch_df(
            venue="fred", symbol=series_id, timeframe=timeframe,
            from_ts=from_ts, to_ts=to_ts, fresh=fresh,
        )
        if df.empty:
            raise ValueError(f"no data for FRED series {series_id}")
        if fresh:
            _panel_cache_put(key, df, {})
        return df["close"]

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
            ids = factor_ids or self._computable_ids(timeframe)
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
            ids = factor_ids or self._computable_ids(timeframe)
            series = self.compute_on_df(df, ids)
            series.update(
                await self._compute_macro(
                    df, timeframe=timeframe, factor_ids=ids, as_of=as_of, fresh=is_live
                )
            )
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
        # 选择效应基准（ADR-0043 D4 延伸）：N 个候选、n_eff 样本下纯噪声能跑出的
        # 期望最大 |IC|。sample_size 取本批最大值（代表性样本量，偏保守的基准）。
        max_samples = max((r["sample_size"] for r in results), default=0)
        benchmark = null_ic_benchmark(len(results), max_samples, horizon_bars)
        return {
            "as_of": as_of,
            "bars_used": len(df),
            "factors": results,
            "ic_null_benchmark": benchmark,
        }, series

    # ── custom（D-12 · 因子发现 L1）──────────────────────────────────
    async def custom_score(
        self,
        *,
        expression: str,
        name: str | None,
        venue: str,
        symbol: str,
        timeframe: str,
        as_of: datetime | None,
        lookback_bars: int,
        horizon_bars: int,
        quantiles: int,
    ) -> dict[str, Any]:
        """自定义表达式因子的一站式评估：求值 → 有效性 → 与库去相关对比。

        一次调用出全套（effectiveness + ic_pvalue + top_correlated），免 LLM 在
        tool 间搬 series 大对象。表达式审计失败由 :class:`expression.ExpressionError`
        冒泡（API 层转 400，message 给 LLM 改写依据）。

        **不走面板缓存**：自定义表达式是任意 key，进单租户公共缓存既会被刷穿
        又有跨用户污染面（engine 缓存注释的单租户假设）。
        """
        parsed = parse_expression(expression)
        now = datetime.now(UTC)
        is_live = as_of is None or as_of >= now - timedelta(
            seconds=_tf_seconds(timeframe) * 2
        )
        as_of = as_of or now
        span_bars = lookback_bars + horizon_bars + 60
        from_ts = as_of - timedelta(seconds=_tf_seconds(timeframe) * span_bars)
        df = await self._fetch_df(
            venue=venue, symbol=symbol, timeframe=timeframe,
            from_ts=from_ts, to_ts=as_of, fresh=is_live,
        )
        if not df.empty and isinstance(df.index, pd.DatetimeIndex):
            df = df[df.index <= pd.Timestamp(as_of)]

        expr_hash = hashlib.sha256(expression.encode("utf-8")).hexdigest()[:16]
        spec = FactorSpec(
            f"custom.{expr_hash}",
            "custom",
            name or (expression if len(expression) <= 60 else expression[:57] + "..."),
            "custom",
            extras={"expression": expression},
        )
        if df.empty:
            return {
                "as_of": as_of,
                "bars_used": 0,
                "available": False,
                "reason": "no bars from data-service",
                "expression": expression,
                "factor": None,
                "ic_pvalue": None,
                "top_correlated": [],
                "max_corr": None,
                "is_likely_redundant": False,
            }

        series = evaluate(parsed, df)
        close = df["close"].astype(float)
        eff = score_factor(
            series,
            close,
            horizon=horizon_bars,
            quantiles=quantiles,
            min_samples=self._settings.min_effective_samples,
        )
        pval = ic_pvalue(eff.rank_ic, eff.sample_size, horizon_bars)

        # 与库内全部价量因子（同 df 现算）做 |spearman| 对比——挡换皮重复因子。
        # macro 源不参与（需另拉 FRED，且与价量表达式天然低相关，省一次外呼）；
        # 按实际 timeframe 取因子，仅显式排除 macro，1d/1wk 的 daily 价量因子照样进库。
        lib = self.compute_on_df(df, self._computable_ids(timeframe, exclude_macro=True))
        corrs: list[tuple[str, float]] = []
        for fid, s in lib.items():
            c = _abs_spearman(series, s)
            if c is not None:
                corrs.append((fid, c))
        corrs.sort(key=lambda t: t[1], reverse=True)
        max_corr = corrs[0][1] if corrs else None
        return {
            "as_of": as_of,
            "bars_used": len(df),
            "available": True,
            "reason": None,
            "expression": expression,
            "factor": _eff_to_dict(spec, eff),
            "ic_pvalue": pval,
            "top_correlated": [
                {"factor_id": fid, "corr": c} for fid, c in corrs[:5]
            ],
            "max_corr": max_corr,
            "is_likely_redundant": bool(
                max_corr is not None
                and max_corr >= self._settings.snapshot_corr_threshold
            ),
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
        # available 语义 = "计算是否成功",**不等于"有可用信号"** —— 全部因子低置信时
        # available=True 但 top_factors=[](research 靠这个三态区分"服务挂了" vs
        # "样本不足",勿改成 len(top)>0)。为防 agent 把 available=True 误读成有信号,
        # top 为空时必填 reason 说明原因,caller 不用猜。
        if scored["bars_used"] <= 0:
            reason: str | None = "no bars from data-service"
        elif not top:
            # 去相关(_select_decorrelated)对非空输入至少保留 1 个 → top 为空
            # ⇔ 没有任何因子过置信阈值。reason 给明确行动方向(加 bar 数),
            # agent 不用在"降相关阈值"与"取更多数据"之间猜。
            reason = (
                f"all_low_confidence: {len(factors)} factors evaluated, none "
                "passed confidence threshold (insufficient sample, need more bars)"
            )
        else:
            reason = None
        return {
            "as_of": scored["as_of"],
            "bars_used": scored["bars_used"],
            "available": scored["bars_used"] > 0 and len(factors) > 0,
            "reason": reason,
            "top_factors": top,
            "candidates_evaluated": len(factors),
            "low_confidence_count": len(factors) - len(confident),
            "ic_null_benchmark": scored["ic_null_benchmark"],
        }

    # ── 横截面 panel──────────────────────────────
    async def panel_score(
        self,
        *,
        symbols: list[str],
        venue: str,
        timeframe: str,
        as_of: datetime | None,
        lookback_bars: int,
        horizon_bars: int,
        factor_ids: list[str] | None,
        min_symbols: int,
    ) -> dict[str, Any]:
        """横截面因子评估：多标的对齐 → 每因子横截面 rank-IC + 最新横截面排名。

        把因子从"单标的择时信号"用作"横截面选股信号"：
        在 universe 上每期按因子排序，与跨标的前瞻收益求横截面 rank-IC；并给出最近
        一期的排名供选标的（如取 PB 最低者，对应聚宽式轮动）。

        约束：
        - **macro 因子不参与**——全市场单值，某时刻对所有标的相同，无横截面区分度。
        - **universe 非 PIT**（is_pit=False）：用调用方给的固定标的集，历史成分
          快照未建，带存活者偏差风险，显式标注不静默（§3.1）。
        - 对齐缺口留 NaN 不 ffill；某期有效标的 < min_symbols 不排名（D1.1）。
        - **取数 fresh=False（读 DB 缓存，不逐标的 backfill）**：N 标的并发 backfill 会
          叠成雪崩（yfinance 串行 + 60s 超时 → 后段标的回填超时静默用陈数据，横截面混
          时效）。横截面是选股/研究语义（历史回放），统一读缓存保证内部一致；要 to-now
          鲜度由调用方先 backfill（§3.1 历史回放显式 fresh=False，已在 universe_note 留痕）。
        """
        as_of = as_of or datetime.now(UTC)
        span_bars = lookback_bars + horizon_bars + 60
        from_ts = as_of - timedelta(seconds=_tf_seconds(timeframe) * span_bars)

        universe_note = (
            "fixed non-PIT universe (caller-supplied symbol set; no historical "
            "constituent snapshot, so cross-sectional survivorship bias is not "
            "controlled — discount the evidence accordingly). Bars are read from the "
            "data-service cache (not force-refreshed); the latest bar per symbol may "
            "lag as_of — pre-backfill the universe if you need to-now freshness"
        )

        # ② 普通时序因子横截面化：价量/自定义因子，显式排除 macro（无横截面区分度）
        ids = factor_ids or self._computable_ids(timeframe, exclude_macro=True)
        ids = [fid for fid in ids if not fid.startswith("macro.")]
        # ① 内禀横截面因子（needs_universe，含 rank()）
        xs_ids = [s.factor_id for s in self.catalog() if s.needs_universe]
        if factor_ids is not None:
            req = set(factor_ids)
            xs_ids = [fid for fid in xs_ids if fid in req]

        # 请求里全是 macro（或未知 id）→ 没有可横截面评估的因子。显式降级不静默（§3.1）：
        # 否则与"评估了但全部低置信"的正常空响应无法区分，agent 会误读成"此 universe 无信号"
        if factor_ids is not None and not ids and not xs_ids:
            return {
                "as_of": as_of, "symbols": symbols, "bars_used": {},
                "is_pit": False, "universe_note": universe_note,
                "factors": [], "ic_null_benchmark": 0.0,
                "reason": (
                    "all requested factor_ids are macro (or unknown): macro factors have no "
                    "cross-sectional differentiation (single value across all symbols) and "
                    "are excluded from panel ranking — pass price/volume or needs_universe "
                    "factor ids, or omit factor_ids to evaluate all"
                ),
            }

        async def _one(sym: str) -> tuple[str, pd.DataFrame]:
            try:
                # fresh=False：横截面读 DB 缓存，不逐标的 backfill（见 docstring 约束）
                df = await self._fetch_df(
                    venue=venue, symbol=sym, timeframe=timeframe,
                    from_ts=from_ts, to_ts=as_of, fresh=False,
                )
            except Exception as exc:  # 单标的 fetch 失败 → 降级为空，其余标的照算横截面
                logger.warning("panel symbol %s fetch failed: %r", sym, exc)
                return sym, pd.DataFrame()
            if not df.empty and isinstance(df.index, pd.DatetimeIndex):
                df = df[df.index <= pd.Timestamp(as_of)]
            return sym, df

        frames = dict(await asyncio.gather(*[_one(s) for s in symbols]))
        bars_used = {sym: len(df) for sym, df in frames.items()}
        close_panel = align_field(frames, "close")

        if close_panel.empty:
            return {
                "as_of": as_of, "symbols": symbols, "bars_used": bars_used,
                "is_pit": False, "universe_note": universe_note,
                "factors": [], "ic_null_benchmark": 0.0,
                "reason": "no bars for any symbol in universe",
            }

        fwd_panel = forward_return_panel(close_panel, horizon_bars)
        per_symbol = {
            sym: self.compute_on_df(df, ids)
            for sym, df in frames.items()
            if not df.empty
        }
        specs = self._spec_index()
        results: list[dict[str, Any]] = []

        def _xs_result(fid: str, fpanel: pd.DataFrame) -> dict[str, Any]:
            """从一个 time × symbol 因子矩阵算横截面 IC + 最新排名 → 结果行。"""
            mean_ic, icir, n_periods, mean_valid = cross_sectional_ic(
                fpanel, fwd_panel, min_symbols=min_symbols
            )
            _t, ranking = latest_cross_section(fpanel, min_symbols=min_symbols)
            spec = specs.get(fid)
            return {
                "factor_id": fid,
                "source": spec.source if spec else "",
                "name": spec.name if spec else fid,
                "kind": spec.kind if spec else "",
                "ic_kind": "cross_sectional",
                "cross_sectional_ic": mean_ic,
                "icir": icir,
                "n_periods": n_periods,
                "mean_valid_symbols": mean_valid,
                "low_confidence": n_periods < MIN_XS_PERIODS,
                "latest_ranking": [
                    {"symbol": s, "value": v, "rank_pct": rp}
                    for (s, v, rp) in ranking
                ],
            }

        # ② 普通时序因子的横截面化：每期把单标的因子值横向排名
        for fid in ids:
            cols = {sym: fac[fid] for sym, fac in per_symbol.items() if fid in fac}
            if len(cols) < min_symbols:
                continue
            results.append(
                _xs_result(fid, pd.DataFrame(cols).reindex(close_panel.index))
            )

        # ① 内禀横截面因子（needs_universe，含 rank()）：在 OHLCV 面板上原生算
        # （xs_ids 已在上方按 factor_ids 过滤）
        if xs_ids:
            fields = {
                "open": align_field(frames, "open"),
                "high": align_field(frames, "high"),
                "low": align_field(frames, "low"),
                "close": close_panel,
                "volume": align_field(frames, "volume"),
            }
            for a in self._adapters:
                # 与单标的 compute_on_df 一致守门：disabled 源不算（当前只有 Alpha101
                # 实现 compute_cross_sectional 且恒可用，但防后续 disabled 源加此方法）
                if not a.available():
                    continue
                fn = getattr(a, "compute_cross_sectional", None)
                if fn is None:
                    continue
                for fid, matrix in fn(fields, xs_ids).items():
                    aligned = matrix.reindex(close_panel.index)
                    if int(aligned.notna().any().sum()) < min_symbols:
                        continue
                    results.append(_xs_result(fid, aligned))

        results.sort(key=lambda r: abs(r["cross_sectional_ic"]), reverse=True)
        max_periods = max((r["n_periods"] for r in results), default=0)
        benchmark = null_ic_benchmark(len(results), max_periods, horizon_bars)
        return {
            "as_of": as_of, "symbols": symbols, "bars_used": bars_used,
            "is_pit": False, "universe_note": universe_note,
            "factors": results, "ic_null_benchmark": benchmark,
            "reason": None,
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
        "decay_state": eff.decay_state,
    }
