"""``GET /perp/funding`` —— USDT-M 永续 mark price + 当期资金费率。

给 paper perp 记账用:live bar 循环按结算时点计提资金费、用真 mark price 判强平 / equity。
仅 crypto venue（connector 实现 ``fetch_perp_funding_rate``）支持;其余 422。
fapi 不通时 ccxt 抛 → 上层 5xx,paper 侧 fallback(funding=0 + 标注失真)。
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from inalpha_shared.auth import User, get_current_user
from inalpha_shared.errors import InalphaError

from ..connectors import get_connector_for_venue, list_registered_venues
from ..schemas import PerpFundingQuery, PerpFundingResponse

router = APIRouter(tags=["perp"])


class PerpNotSupportedError(InalphaError):
    code = "PERP_NOT_SUPPORTED_FOR_VENUE"
    status_code = 422


@router.get("/perp/funding", response_model=PerpFundingResponse)
async def get_perp_funding(
    _user: Annotated[User, Depends(get_current_user)],
    query: Annotated[PerpFundingQuery, Depends()],
) -> PerpFundingResponse:
    """返回 ``venue/symbol`` 永续的 mark price + 当期 funding rate。"""
    venue = query.venue.strip().lower()
    if venue not in list_registered_venues():
        raise PerpNotSupportedError(
            f"venue {venue!r} not registered", details={"venue": venue}
        )
    conn = get_connector_for_venue(venue)
    fetch = getattr(conn, "fetch_perp_funding_rate", None)
    if fetch is None:
        raise PerpNotSupportedError(
            f"venue {venue!r} 不支持永续 funding(仅 crypto perp)", details={"venue": venue}
        )
    out = await fetch(query.symbol)
    return PerpFundingResponse(
        venue=venue,
        symbol=out["symbol"],
        mark_price=out["mark_price"],
        funding_rate=out["funding_rate"],
        ts=out["ts"],
        next_funding_ts=out["next_funding_ts"],
    )
