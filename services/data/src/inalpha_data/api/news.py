"""``GET /news`` —— 拉新闻头条（D-9，零 key，给 research analyst 喂真数据用）。

当前支持 venue=yfinance（全球）和 venue=akshare（A股）。
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from inalpha_shared.auth import User, get_current_user
from inalpha_shared.errors import ValidationError

from ..connectors import yfinance_conn
from ..connectors._base import get_connector_for_venue
from ..schemas import NewsItem, NewsQuery, NewsResponse

router = APIRouter(tags=["news"])


@router.get("/news", response_model=NewsResponse)
async def get_news(
    _user: Annotated[User, Depends(get_current_user)],
    query: Annotated[NewsQuery, Query()],
) -> NewsResponse:
    """拉指定 ticker 的最新新闻。

    venue 支持 yfinance / akshare（其它返 422）；不支持 ticker 返空 list 而非错。
    """
    if query.venue == "yfinance":
        try:
            conn = yfinance_conn.get_connector()
            raw = await conn.fetch_news(query.symbol, limit=query.limit)
        except Exception:
            raw = []
    elif query.venue == "akshare":
        conn = get_connector_for_venue("akshare")
        if not hasattr(conn, "fetch_news"):
            raise ValidationError(
                f"news fetch not available for venue {query.venue!r}",
                code="NEWS_FETCH_NOT_SUPPORTED",
                details={"venue": query.venue},
            )
        raw = await conn.fetch_news(query.symbol, limit=query.limit)  # type: ignore[union-attr]
    else:
        raise ValidationError(
            f"news venue {query.venue!r} not supported",
            code="NEWS_VENUE_NOT_SUPPORTED",
            details={"venue": query.venue, "supported": ["yfinance", "akshare"]},
        )

    items = [NewsItem(**r) for r in raw]
    return NewsResponse(venue=query.venue, symbol=query.symbol, items=items)
