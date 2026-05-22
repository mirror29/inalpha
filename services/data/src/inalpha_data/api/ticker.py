"""``GET /ticker`` —— 单次最新价查询，D-8a' 加。

设计动机：

paper ``/orders/submit`` 撮合时需要一个"当前合理价"做 ref，原本是让 orchestration 层
（LLM）调 ``data.get_bars(limit=1)`` 取再传——但 LLM 可能 hallucinate / 拿到 stale 数据，
而且 backfill 慢（CCXT rate-limited 大跨度 fetch_ohlcv 可能分钟级）会拖累下单链路。

本端点把"取最新价"职责从客户端（LLM）拉回服务端：

1. 从 DB 拿最新一根 1m bar.close（最准）
2. 1m 没有就降级到 1h
3. 都没有 → 404 NO_PRICE_AVAILABLE，由 caller（paper）决定怎么办

**不**直接调 Binance ticker API：
- 现阶段每个标的的 1h backfill 已经常驻；DB stale 度一般 < 1 小时
- 加 Binance call 引入网络抖动（截图里就是这个问题）
- D-9+ 接 WS 订阅后用最新 tick 替代

返回 ``is_stale=true`` 时 caller 决定是否信任（paper 当前都信，后续可加阈值拒绝）。
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends
from inalpha_shared.auth import User, get_current_user
from inalpha_shared.db import DBConn
from inalpha_shared.errors import InalphaError

from ..schemas import TickerQuery, TickerResponse
from ..storage.bars import query_bars

router = APIRouter(tags=["ticker"])

# 5 分钟以内的数据视为"新鲜"，超过则 is_stale=true（caller 决定信不信）
STALE_THRESHOLD_SECONDS = 300

# 查询时回看 24 小时（足够拿到最新一根 1m 或 1h；再老就当 NO_PRICE）
LOOKBACK_HOURS = 24


class NoPriceAvailableError(InalphaError):
    code = "NO_PRICE_AVAILABLE"
    status_code = 404


@router.get("/ticker", response_model=TickerResponse)
async def get_ticker(
    db: DBConn,
    _user: Annotated[User, Depends(get_current_user)],
    query: Annotated[TickerQuery, Depends()],
) -> TickerResponse:
    """返回 ``venue/symbol`` 的最新价。

    优先级 1m → 1h；都没有抛 ``NO_PRICE_AVAILABLE``。
    """
    now = datetime.now(UTC)
    lookback_start = now - timedelta(hours=LOOKBACK_HOURS)

    # 优先 1m
    for timeframe, source_tag in (("1m", "db_1m"), ("1h", "db_1h")):
        rows = await query_bars(
            db,
            venue=query.venue,
            symbol=query.symbol,
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
