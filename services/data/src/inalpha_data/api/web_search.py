"""GET /web/search and GET /web/news — ddgs metasearch endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from inalpha_shared import get_logger
from inalpha_shared.auth import User, get_current_user

from ..connectors.web_search import get_connector
from ..schemas import WebSearchResponse, WebSearchResult

_logger = get_logger(__name__)
router = APIRouter(tags=["web_search"])


@router.get("/web/search", response_model=WebSearchResponse)
async def web_search(
    _user: Annotated[User, Depends(get_current_user)],
    query: Annotated[str, Query(description="Search query string")],
    backend: Annotated[str, Query(description="auto/bing/duckduckgo/google/brave")] = "auto",
    max_results: Annotated[int, Query(ge=1, le=20, description="Max results 1-20")] = 10,
) -> WebSearchResponse:
    try:
        conn = get_connector()
        items = await conn.fetch_search(query=query, backend=backend, max_results=max_results)
    except Exception as exc:
        _logger.warning("web_search_endpoint_failed", query=query, error=str(exc))
        return WebSearchResponse(query=query, backend=backend, results=[])
    return WebSearchResponse(
        query=query,
        backend=backend,
        results=[
            WebSearchResult(
                title=r.get("title", ""),
                url=r.get("href", ""),
                snippet=r.get("body", ""),
            )
            for r in items
        ],
    )


@router.get("/web/news", response_model=WebSearchResponse)
async def web_search_news(
    _user: Annotated[User, Depends(get_current_user)],
    query: Annotated[str, Query(description="News search query")],
    max_results: Annotated[int, Query(ge=1, le=20)] = 10,
) -> WebSearchResponse:
    try:
        conn = get_connector()
        items = await conn.fetch_news(query=query, max_results=max_results)
    except Exception as exc:
        _logger.warning("web_search_news_endpoint_failed", query=query, error=str(exc))
        return WebSearchResponse(query=query, backend="news", results=[])
    return WebSearchResponse(
        query=query,
        backend="news",
        results=[
            WebSearchResult(
                title=r.get("title", ""),
                url=r.get("href", ""),
                snippet=r.get("body", ""),
            )
            for r in items
        ],
    )
