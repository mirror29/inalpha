"""``GET /fundamentals`` —— 拉财报基本面数据（D-10，给 research analyst 用）。

支持 venue=baostock（A股）和 venue=yfinance（全球兜底）。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from inalpha_shared.auth import User, get_current_user
from inalpha_shared.errors import ValidationError

from ..connectors import yfinance_conn
from ..connectors._base import get_connector_for_venue
from ..schemas import FinancialsResponse
from ..venues import canonicalize_market_identity

router = APIRouter(tags=["fundamentals"])


@router.get("/fundamentals", response_model=FinancialsResponse)
async def get_fundamentals(
    _user: Annotated[User, Depends(get_current_user)],
    venue: Annotated[str, Query(description="数据源：baostock 或 yfinance")],
    symbol: Annotated[str, Query(description="ticker 标识（如 sh.600519 / AAPL）")],
    as_of: Annotated[
        str | None,
        Query(
            description="point-in-time 截断（ISO 8601，ADR-0053 阶段 A）：只返回该时点已"
            "披露的财报，防回测看到未来财报。仅 baostock 生效；yfinance v1 不做 PIT。",
        ),
    ] = None,
) -> FinancialsResponse:
    """拉指定 ticker 的财报基本面数据（``as_of`` 给定则做 PIT 截断）。

    venue 支持 baostock / yfinance；其它 venue 返 422。
    """
    effective_venue, effective_symbol = canonicalize_market_identity(venue, symbol)
    if effective_venue == "yfinance":
        try:
            conn = yfinance_conn.get_connector()
            data = await conn.fetch_financials(effective_symbol, as_of=as_of)
        except Exception as exc:
            return FinancialsResponse(
                venue=venue,
                symbol=symbol,
                available=False,
                reason=f"yfinance connector failed: {exc}",
            )
        return FinancialsResponse(**data)

    if effective_venue == "baostock":
        conn = get_connector_for_venue("baostock")
        if not hasattr(conn, "fetch_financials"):
            raise ValidationError(
                f"fundamentals fetch not available for venue {venue!r}",
                code="FUNDAMENTALS_NOT_SUPPORTED",
                details={"venue": venue},
            )
        data = await conn.fetch_financials(effective_symbol, as_of=as_of)  # type: ignore[union-attr]
        return FinancialsResponse(**data)

    raise ValidationError(
        f"fundamentals venue {venue!r} not supported",
        code="FUNDAMENTALS_VENUE_NOT_SUPPORTED",
        details={"venue": venue, "supported": ["baostock", "yfinance"]},
    )
