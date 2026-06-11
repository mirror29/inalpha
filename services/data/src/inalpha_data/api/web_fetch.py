"""GET /web/fetch — 网页正文抓取端点（证据链：URL → 可引用正文）。"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from inalpha_shared import get_logger
from inalpha_shared.auth import User, get_current_user

from ..connectors.web_fetch import get_connector
from ..schemas import WebFetchResponse

_logger = get_logger(__name__)
router = APIRouter(tags=["web_fetch"])


@router.get("/web/fetch", response_model=WebFetchResponse)
async def web_fetch(
    _user: Annotated[User, Depends(get_current_user)],
    url: Annotated[str, Query(description="http/https URL to fetch", max_length=2048)],
    max_chars: Annotated[
        int | None,
        Query(ge=100, le=200_000, description="正文字符上限（受服务端上限钳制）"),
    ] = None,
) -> WebFetchResponse:
    try:
        conn = get_connector()
        out = await conn.fetch_page(url=url, max_chars=max_chars)
    except Exception as exc:
        _logger.warning("web_fetch_endpoint_failed", url=url[:200], error=str(exc))
        return WebFetchResponse(url=url, error=str(exc))
    return WebFetchResponse(**out)
