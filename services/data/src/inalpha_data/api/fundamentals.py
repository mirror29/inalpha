"""``GET /fundamentals`` —— 拉财报基本面数据（D-10，给 research analyst 用）。

支持 venue=akshare（A股/港股）和 venue=yfinance（全球兜底）。
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from inalpha_shared.auth import User, get_current_user
from inalpha_shared.errors import ValidationError

from ..connectors import yfinance_conn
from ..connectors._base import get_connector_for_venue
from ..schemas import FinancialsResponse

router = APIRouter(tags=["fundamentals"])


@router.get("/fundamentals", response_model=FinancialsResponse)
async def get_fundamentals(
    _user: Annotated[User, Depends(get_current_user)],
    venue: Annotated[str, Query(description="数据源：akshare 或 yfinance")],
    symbol: Annotated[str, Query(description="ticker 标识（如 sh.600519 / AAPL）")],
) -> FinancialsResponse:
    """拉指定 ticker 的最新财报基本面数据。

    venue 支持 akshare / yfinance；其它 venue 返 422。
    """
    if venue == "yfinance":
        try:
            conn = yfinance_conn.get_connector()
            data = await conn.fetch_financials(symbol)
        except Exception as exc:
            return FinancialsResponse(
                venue=venue,
                symbol=symbol,
                available=False,
                reason=f"yfinance connector failed: {exc}",
            )
        return FinancialsResponse(**data)

    if venue == "akshare":
        conn = get_connector_for_venue("akshare")
        if not hasattr(conn, "fetch_financials"):
            raise ValidationError(
                f"fundamentals fetch not available for venue {venue!r}",
                code="FUNDAMENTALS_NOT_SUPPORTED",
                details={"venue": venue},
            )
        data = await conn.fetch_financials(symbol)  # type: ignore[union-attr]
        return FinancialsResponse(**data)

    raise ValidationError(
        f"fundamentals venue {venue!r} not supported",
        code="FUNDAMENTALS_VENUE_NOT_SUPPORTED",
        details={"venue": venue, "supported": ["akshare", "yfinance"]},
    )

