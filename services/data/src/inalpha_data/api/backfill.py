"""``POST /backfill/bars`` —— 从外部市场拉历史 K 线落库。

D-9 起多 venue：按 ``req.venue`` 从注册表取 connector（binance / alpaca / baostock）。
"""

from __future__ import annotations

from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends
from inalpha_shared import get_logger
from inalpha_shared.auth import User, get_current_user
from inalpha_shared.db import DBConn
from inalpha_shared.errors import InalphaError, ValidationError

from ..connectors import Connector, get_connector_for_venue, list_registered_venues
from ..connectors.alpaca import TIMEFRAME_SECONDS as ALPACA_TIMEFRAME_SECONDS
from ..connectors.binance import TIMEFRAME_SECONDS as BINANCE_TIMEFRAME_SECONDS
from ..connectors.fred import TIMEFRAME_SECONDS as FRED_TIMEFRAME_SECONDS
from ..connectors.yfinance_conn import TIMEFRAME_SECONDS as YFINANCE_TIMEFRAME_SECONDS
from ..schemas import BackfillRequest, BackfillResponse
from ..storage.bars import insert_bars, latest_bar_ts
from ..venues import canonicalize_market_identity, is_legacy_a_share_venue

router = APIRouter(tags=["backfill"])
_logger = get_logger(__name__)

# 单次 fetch 上限（与 binance CCXT 默认 batch 一致；多 venue 共用）
_BATCH_LIMIT = 1000

# 跨度硬限：跨度 * timeframe 太大就拒绝
# D-8b' review 高风险 #6：长跨度同步 backfill 会卡死请求线程
_MAX_BARS_PER_REQUEST = 50_000

# 分钟级查询限制（腾讯行情请求量与同步延迟保护）
# 腾讯分钟 K 单请求有返回条数上限；长跨度既无法完整返回，也会占用串行抓取通道。
_MINUTE_LOOKBACK_LIMITS = {
    "5m": 7,  # 7 天 = 336 条
    "15m": 14,  # 14 天 = 672 条
    "30m": 30,  # 30 天 = 720 条
    "1h": 60,  # 60 天 = 720 条
}


class BarsUpstreamUnavailableError(InalphaError):
    """外部 K 线源不可用，避免把网络故障伪装成成功的空结果。"""

    code = "BARS_UPSTREAM_UNAVAILABLE"
    status_code = 502


# venue → 该 venue 支持的 timeframe → 秒数
# baostock（baostock venue）支持日级 + 分钟级（5/15/30/60 分钟）
_VENUE_TIMEFRAME_SECONDS: dict[str, dict[str, int]] = {
    "binance": BINANCE_TIMEFRAME_SECONDS,
    "alpaca": ALPACA_TIMEFRAME_SECONDS,
    "baostock": {
        "1d": 86400,
        "1wk": 604800,
        "1mo": 2_592_000,
        # 分钟级（baostock 支持）
        "5m": 300,
        "15m": 900,
        "30m": 1800,
        "1h": 3600,
    },
    "yfinance": YFINANCE_TIMEFRAME_SECONDS,
    "fred": FRED_TIMEFRAME_SECONDS,
}


@router.post("/backfill/bars", response_model=BackfillResponse)
async def backfill_bars(
    req: BackfillRequest,
    db: DBConn,
    _user: Annotated[User, Depends(get_current_user)],
) -> BackfillResponse:
    """从外部 venue 拉指定时段的 K 线，幂等写入 TimescaleDB。

    支持 venue：``binance`` / ``alpaca`` / ``baostock``。

    **硬限**：``(to_ts - from_ts) / timeframe`` 估算 bar 数 > 50k 直接拒。
    """
    if req.from_ts > req.to_ts:
        raise ValidationError("from_ts must be <= to_ts")

    # ─── venue 路由 ──────────────────────────────────────────────
    # **向后兼容**：venue="akshare" 且 symbol 带 sh./sz. 前缀 → 自动路由到 baostock
    effective_venue, effective_symbol = canonicalize_market_identity(req.venue, req.symbol)
    if is_legacy_a_share_venue(req.venue, req.symbol):
        _logger.warning(
            "venue_akshare_deprecated",
            symbol=req.symbol,
            reason="venue 'akshare' is deprecated for A-share; use 'baostock' instead",
        )

    try:
        connector: Connector = get_connector_for_venue(effective_venue)
    except KeyError:
        raise ValidationError(
            f"unsupported venue {req.venue!r}",
            details={"supported": list_registered_venues()},
        ) from None

    tf_table = _VENUE_TIMEFRAME_SECONDS.get(effective_venue)
    if tf_table is None or req.timeframe not in tf_table:
        raise ValidationError(
            f"venue {req.venue!r} does not support timeframe {req.timeframe!r}",
            details={
                "venue": req.venue,
                "supported_timeframes": sorted((tf_table or {}).keys()),
            },
        )

    # ─── 分钟级强制限制（腾讯行情请求量与同步延迟保护）─────────────────
    # canonical baostock venue 的分钟线由腾讯 HTTPS 提供，限制长跨度无效拉取。
    # 仅对 baostock venue 生效（binance/alpaca/yfinance 不受此限制）
    effective_from_ts = req.from_ts
    if effective_venue == "baostock" and req.timeframe in _MINUTE_LOOKBACK_LIMITS:
        max_lookback_days = _MINUTE_LOOKBACK_LIMITS[req.timeframe]
        span_days = (req.to_ts - req.from_ts).total_seconds() / 86400

        if span_days > max_lookback_days:
            # 强制截断到允许的最大范围
            effective_from_ts = req.to_ts - timedelta(days=max_lookback_days)
            _logger.warning(
                "backfill_minute_lookback_capped",
                venue=req.venue,
                symbol=effective_symbol,
                timeframe=req.timeframe,
                original_from_ts=req.from_ts.isoformat(),
                capped_from_ts=effective_from_ts.isoformat(),
                max_lookback_days=max_lookback_days,
                reason="minute kline request bound",
            )

    span_seconds = (req.to_ts - effective_from_ts).total_seconds()
    tf_seconds = tf_table[req.timeframe]
    estimated_bars = int(span_seconds / tf_seconds)
    if estimated_bars > _MAX_BARS_PER_REQUEST:
        raise ValidationError(
            f"requested span too large: ~{estimated_bars} bars > limit {_MAX_BARS_PER_REQUEST}; "
            f"split into smaller windows or use larger timeframe",
            code="BACKFILL_SPAN_TOO_LARGE",
            details={
                "estimated_bars": estimated_bars,
                "max_bars": _MAX_BARS_PER_REQUEST,
                "timeframe": req.timeframe,
                "span_seconds": int(span_seconds),
            },
        )

    # ─── 增量续拉 ────────────────────────────────────────────────
    # 已缓存到哪根就从哪根继续，只补缺口；缓存覆盖大半时只拉最近几根
    # （而非每次把整个 [from_ts, to_ts] 从外部 venue 全量重拉 —— CCXT 限流下
    # 长窗口会超时）。仍循环拉到 to_ts，故尾部始终补到当前、不牺牲新鲜度。
    # 起点取已缓存 max(ts)（重拉最后一根，覆盖落库时仍未收盘的半根 candle），
    # 但不早于请求的 from_ts；空缓存则从 from_ts 全量。
    # 注：仅按 max(ts) 续拉，中间空洞（非连续缓存，罕见）不会回补；需要时显式重拉窗口。
    cached_latest = await latest_bar_ts(
        db, effective_venue, effective_symbol, req.timeframe, upto=req.to_ts
    )
    if cached_latest is not None and cached_latest > effective_from_ts:
        cursor = cached_latest
        _logger.info(
            "backfill_incremental",
            venue=req.venue,
            symbol=req.symbol,
            timeframe=req.timeframe,
            cached_latest=cached_latest.isoformat(),
            from_ts=effective_from_ts.isoformat(),
        )
    else:
        cursor = effective_from_ts
    fetched_total = 0
    inserted_total = 0

    while cursor < req.to_ts:
        try:
            bars = await connector.fetch_bars(
                symbol=effective_symbol,
                timeframe=req.timeframe,
                since=cursor,
                limit=_BATCH_LIMIT,
            )
        except Exception as exc:
            _logger.warning(
                "backfill_connector_failed",
                venue=req.venue,
                symbol=effective_symbol,
                error=str(exc),
                cursor=cursor.isoformat(),
            )
            raise BarsUpstreamUnavailableError(
                f"bars source unavailable for {effective_symbol}@{effective_venue}",
                details={
                    "venue": req.venue,
                    "symbol": req.symbol,
                    "timeframe": req.timeframe,
                    "reason": str(exc),
                },
            ) from exc
        if not bars:
            if fetched_total == 0:
                # 首批就空——上游根本没返数据，比增量结束严重
                _logger.warning(
                    "backfill_no_more_bars_first_batch_empty",
                    venue=req.venue,
                    symbol=effective_symbol,
                    timeframe=req.timeframe,
                    cursor=cursor.isoformat(),
                )
            else:
                _logger.info(
                    "backfill_no_more_bars",
                    venue=req.venue,
                    symbol=effective_symbol,
                    cursor=cursor.isoformat(),
                )
            break

        # 过滤掉超过 to_ts 的 bar
        bars = [b for b in bars if b[0] <= req.to_ts]
        if not bars:
            break

        n = await insert_bars(db, effective_venue, effective_symbol, req.timeframe, bars)
        fetched_total += len(bars)
        inserted_total += n

        last_ts = bars[-1][0]
        next_cursor = last_ts + timedelta(seconds=tf_seconds)
        if next_cursor <= cursor:
            # 兜底：游标没推进就停，防止死循环
            _logger.warning(
                "backfill_cursor_stuck",
                venue=req.venue,
                cursor=cursor.isoformat(),
                last_ts=last_ts.isoformat(),
            )
            break
        cursor = next_cursor

    _logger.info(
        "backfill_done",
        venue=req.venue,
        symbol=req.symbol,
        timeframe=req.timeframe,
        fetched=fetched_total,
        inserted=inserted_total,
    )

    return BackfillResponse(
        venue=req.venue,
        symbol=req.symbol,
        timeframe=req.timeframe,
        bars_fetched=fetched_total,
        bars_inserted=inserted_total,
        from_ts=effective_from_ts,
        to_ts=req.to_ts,
    )
