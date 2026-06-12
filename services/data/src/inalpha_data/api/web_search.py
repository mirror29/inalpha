"""GET /web/search and GET /web/news — ddgs metasearch endpoints."""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from inalpha_shared import get_logger
from inalpha_shared.auth import User, get_current_user

from ..connectors.web_search import SearchOutcome, get_connector
from ..schemas import WebSearchResponse, WebSearchResult

_logger = get_logger(__name__)
router = APIRouter(tags=["web_search"])


def _to_response(query: str, requested_backend: str, outcome: SearchOutcome) -> WebSearchResponse:
    return WebSearchResponse(
        query=query,
        backend=outcome.backend_used or requested_backend,
        status=outcome.status,  # type: ignore[arg-type]
        error=outcome.error,
        hint=outcome.hint,
        fetched_at=datetime.now(UTC).isoformat(),
        results=[
            WebSearchResult(
                title=r.get("title", ""),
                url=r.get("href", ""),
                snippet=r.get("body", ""),
            )
            for r in outcome.results
        ],
    )


@router.get("/web/search", response_model=WebSearchResponse)
async def web_search(
    _user: Annotated[User, Depends(get_current_user)],
    query: Annotated[str, Query(description="Search query string")],
    backend: Annotated[str, Query(description="auto/bing/duckduckgo/google/brave")] = "auto",
    max_results: Annotated[int, Query(ge=1, le=20, description="Max results 1-20")] = 10,
) -> WebSearchResponse:
    try:
        conn = get_connector()
        outcome = await conn.fetch_search(query=query, backend=backend, max_results=max_results)
    except Exception as exc:
        # 端点不 500（搜索是尽力而为的增强项），但失败原因必须带回——
        # 静默吞成空数组让 agent 把"故障"当"无证据"用，正是本次修复的根因。
        _logger.warning("web_search_endpoint_failed", query=query, error=str(exc))
        outcome = SearchOutcome(status="engine_error", error=str(exc))
    return _to_response(query, backend, outcome)


@router.get("/web/news", response_model=WebSearchResponse)
async def web_search_news(
    _user: Annotated[User, Depends(get_current_user)],
    query: Annotated[str, Query(description="News search query")],
    max_results: Annotated[int, Query(ge=1, le=20)] = 10,
) -> WebSearchResponse:
    try:
        conn = get_connector()
        outcome = await conn.fetch_news(query=query, max_results=max_results)
    except Exception as exc:
        _logger.warning("web_search_news_endpoint_failed", query=query, error=str(exc))
        outcome = SearchOutcome(status="engine_error", error=str(exc))
    return _to_response(query, "news", outcome)
