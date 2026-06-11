"""GET /symbols/search — 公司名 / 关键词 → ticker 解析端点。"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from inalpha_shared import get_logger
from inalpha_shared.auth import User, get_current_user

from ..connectors.symbol_search import get_connector
from ..schemas import SymbolSearchResponse, SymbolSearchResult

_logger = get_logger(__name__)
router = APIRouter(tags=["symbols"])


@router.get("/symbols/search", response_model=SymbolSearchResponse)
async def symbols_search(
    _user: Annotated[User, Depends(get_current_user)],
    query: Annotated[str, Query(min_length=1, max_length=80, description="公司名 / 代码片段")],
    venue: Annotated[
        str, Query(description="auto / akshare（A股表）/ yfinance（Yahoo 全球）")
    ] = "auto",
    max_results: Annotated[int, Query(ge=1, le=20)] = 10,
) -> SymbolSearchResponse:
    try:
        conn = get_connector()
        items = await conn.search(query=query, venue=venue, max_results=max_results)
    except Exception as exc:
        _logger.warning("symbols_search_endpoint_failed", query=query[:80], error=str(exc))
        return SymbolSearchResponse(query=query, results=[])
    return SymbolSearchResponse(
        query=query,
        results=[SymbolSearchResult(**item) for item in items],
    )
