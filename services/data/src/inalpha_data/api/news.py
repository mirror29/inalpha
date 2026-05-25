"""``GET /news`` —— 拉新闻头条（D-9，零 key，给 research analyst 喂真数据用）。

当前仅 venue=yfinance 支持；akshare/fred/alpaca/binance 都没新闻接口。
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from inalpha_shared.auth import User, get_current_user
from inalpha_shared.errors import ValidationError

from ..connectors import yfinance_conn
from ..schemas import NewsItem, NewsQuery, NewsResponse

router = APIRouter(tags=["news"])


@router.get("/news", response_model=NewsResponse)
async def get_news(
    _user: Annotated[User, Depends(get_current_user)],
    query: Annotated[NewsQuery, Query()],
) -> NewsResponse:
    """拉指定 ticker 的最新新闻。

    venue 仅支持 yfinance（其它返 422）；不支持 ticker 返空 list 而非错。
    """
    if query.venue != "yfinance":
        raise ValidationError(
            f"news venue {query.venue!r} not supported; only 'yfinance' for D-9",
            code="NEWS_VENUE_NOT_SUPPORTED",
            details={"venue": query.venue, "supported": ["yfinance"]},
        )

    conn = yfinance_conn.get_connector()
    raw = await conn.fetch_news(query.symbol, limit=query.limit)
    items = [NewsItem(**r) for r in raw]
    return NewsResponse(venue=query.venue, symbol=query.symbol, items=items)
