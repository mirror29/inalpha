"""``GET /bars`` —— 从 TimescaleDB 查 K 线。"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from inalpha_shared import get_logger
from inalpha_shared.auth import User, get_current_user
from inalpha_shared.db import DBConn
from inalpha_shared.errors import ValidationError

from ..schemas import BarResponse, BarsQuery
from ..storage.bars import query_bars

router = APIRouter(tags=["bars"])
_logger = get_logger(__name__)


@router.get("/bars", response_model=list[BarResponse])
async def list_bars(
    db: DBConn,
    _user: Annotated[User, Depends(get_current_user)],
    query: Annotated[BarsQuery, Query()],
) -> list[BarResponse]:
    """按 ``venue / symbol / timeframe / from_ts / to_ts`` 查 K 线。

    时间范围闭区间，最多返回 ``limit`` 根（默认 10k，上限 50k）。
    """
    if query.from_ts > query.to_ts:
        raise ValidationError("from_ts must be <= to_ts", details={
            "from_ts": query.from_ts.isoformat(),
            "to_ts": query.to_ts.isoformat(),
        })

        # 向后兼容：venue="akshare" + sh./sz. 前缀 → 自动用 baostock 查 DB
    _baostock_prefixes = ("sh.", "sz.")
    effective_venue = query.venue
    if query.venue == "akshare" and any(query.symbol.startswith(p) for p in _baostock_prefixes):
        _logger.warning(
            "venue_akshare_deprecated",
            symbol=query.symbol,
            reason="venue 'akshare' is deprecated for A-share; use 'baostock' instead",
        )
        effective_venue = "baostock"

    rows = await query_bars(
        db,
        venue=effective_venue,
        symbol=query.symbol,
        timeframe=query.timeframe,
        from_ts=query.from_ts,
        to_ts=query.to_ts,
        limit=query.limit,
    )
    return [BarResponse(**row) for row in rows]
