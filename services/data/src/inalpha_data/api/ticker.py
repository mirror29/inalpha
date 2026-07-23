"""``GET /ticker`` —— 单次最新价查询，D-8a' 加，D-9 扩多 venue。

设计动机：

paper ``/orders/submit`` 撮合时需要一个"当前合理价"做 ref，原本是让 orchestration 层
（LLM）调 ``data.get_bars(limit=1)`` 取再传——但 LLM 可能 hallucinate / 拿到 stale 数据，
而且 backfill 慢（CCXT rate-limited 大跨度 fetch_ohlcv 可能分钟级）会拖累下单链路。

本端点把"取最新价"职责从客户端（LLM）拉回服务端：

1. 从 DB 拿最新一根 1m bar.close（最准）
2. 1m 没有就降级到 1h
3. 都没有 → 404 NO_PRICE_AVAILABLE，由 caller（paper）决定怎么办

D-9 ``fresh=true`` 路由从硬编码 binance 改成**按 connector capability 鸭子分发**：

- venue 在注册表 + connector 实现了 ``TickerCapable`` Protocol（含 ``fetch_ticker``）
  → 调该 venue 的 fetch_ticker（binance / yfinance / alpaca / baostock 当前实现）
- venue 在注册表但 connector 无 fetch_ticker（fred）→ 返
  ``FRESH_NOT_SUPPORTED_FOR_VENUE``，引导 caller 切 fresh=false 走 DB cache
- venue 未注册 → 422 unsupported venue（与 ``/backfill/bars`` 一致的错误形态）

返回 ``is_stale=true`` 时 caller 决定是否信任（paper 当前都信，后续可加阈值拒绝）。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends
from inalpha_shared.auth import User, get_current_user
from inalpha_shared.db import DBConn
from inalpha_shared.errors import InalphaError, ValidationError

from ..connectors import (
    TickerCapable,
    get_connector_for_venue,
    list_registered_venues,
)
from ..schemas import TickerQuery, TickerResponse
from ..storage.bars import query_bars
from ..venues import canonicalize_market_identity

router = APIRouter(tags=["ticker"])

# 5 分钟以内的数据视为"新鲜"，超过则 is_stale=true（caller 决定信不信）
STALE_THRESHOLD_SECONDS = 300

# 查询时回看 24 小时（足够拿到最新一根 1m 或 1h；再老就当 NO_PRICE）
LOOKBACK_HOURS = 24


class NoPriceAvailableError(InalphaError):
    code = "NO_PRICE_AVAILABLE"
    status_code = 404


class FreshNotSupportedError(InalphaError):
    code = "FRESH_NOT_SUPPORTED_FOR_VENUE"
    status_code = 422


class TickerUnavailableError(InalphaError):
    """外部数据源实时报价不可用（限流 / 超时 / 网络分区），非代码 bug。"""

    code = "TICKER_UNAVAILABLE"
    status_code = 502


@router.get("/ticker", response_model=TickerResponse)
async def get_ticker(
    db: DBConn,
    _user: Annotated[User, Depends(get_current_user)],
    query: Annotated[TickerQuery, Depends()],
) -> TickerResponse:
    """返回 ``venue/symbol`` 的最新价。

    - ``fresh=false``（默认）：DB 优先 1m → 1h；都没有抛 ``NO_PRICE_AVAILABLE``。
    - ``fresh=true``：调 venue 实时 ticker；venue connector 须实现 ``TickerCapable``
      （当前 binance / yfinance / alpaca / baostock）。fred 不实现则返
      ``FRESH_NOT_SUPPORTED_FOR_VENUE`` 引导走 fresh=false。
      网络抖动失败时**不**自动 fallback 到 DB（让 caller 看到原因），由 caller 决定重试。
    """
    now = datetime.now(UTC)
    effective_venue, effective_symbol = canonicalize_market_identity(query.venue, query.symbol)

    if query.fresh:
        try:
            connector = get_connector_for_venue(effective_venue)
        except KeyError:
            raise ValidationError(
                f"unsupported venue {query.venue!r}",
                details={"supported": list_registered_venues()},
            ) from None
        if not isinstance(connector, TickerCapable):
            raise FreshNotSupportedError(
                f"fresh=true not supported for venue {query.venue!r}",
                details={
                    "venue": query.venue,
                    "hint": "use fresh=false to read latest DB cache (run /backfill/bars first if empty)",
                },
            )
        try:
            ts, price = await connector.fetch_ticker(effective_symbol)
        except RuntimeError as exc:
            raise TickerUnavailableError(
                str(exc),
                details={
                    "venue": query.venue,
                    "symbol": query.symbol,
                    "hint": "use fresh=false to read latest DB cache (run /backfill/bars first if empty)",
                },
            ) from exc
        stale_seconds = max(int((now - ts).total_seconds()), 0)
        return TickerResponse(
            venue=query.venue,
            symbol=query.symbol,
            price=price,
            ts=ts,
            source=f"{query.venue}_ticker",
            is_stale=stale_seconds > STALE_THRESHOLD_SECONDS,
            stale_seconds=stale_seconds,
        )

    lookback_start = now - timedelta(hours=LOOKBACK_HOURS)

    # 优先 1m
    for timeframe, source_tag in (("1m", "db_1m"), ("1h", "db_1h")):
        rows = await query_bars(
            db,
            venue=effective_venue,
            symbol=effective_symbol,
            timeframe=timeframe,
            from_ts=lookback_start,
            to_ts=now,
            limit=1,  # query_bars 现在取最新 1 根（ASC bug 已修）
        )
        if rows:
            row = rows[-1]  # 安全起见取最后一根
            bar_ts = row["ts"]
            if bar_ts.tzinfo is None:
                bar_ts = bar_ts.replace(tzinfo=UTC)
            stale_seconds = int((now - bar_ts).total_seconds())
            return TickerResponse(
                venue=query.venue,
                symbol=query.symbol,
                price=float(row["close"]),
                ts=bar_ts,
                source=source_tag,
                is_stale=stale_seconds > STALE_THRESHOLD_SECONDS,
                stale_seconds=max(stale_seconds, 0),
            )

    raise NoPriceAvailableError(
        f"no price available for {query.symbol}@{query.venue} in last {LOOKBACK_HOURS}h",
        details={
            "venue": query.venue,
            "symbol": query.symbol,
            "lookback_hours": LOOKBACK_HOURS,
            "hint": "call POST /backfill/bars first (timeframe='1h', from_ts=24h ago)",
        },
    )
