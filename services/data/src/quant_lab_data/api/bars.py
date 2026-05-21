"""``GET /bars`` —— 从 TimescaleDB 查 K 线。"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from quant_lab_shared.auth import User, get_current_user
from quant_lab_shared.db import DBConn
from quant_lab_shared.errors import ValidationError

from ..schemas import BarResponse, BarsQuery
from ..storage.bars import query_bars

router = APIRouter(tags=["bars"])


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

    rows = await query_bars(
        db,
        venue=query.venue,
        symbol=query.symbol,
        timeframe=query.timeframe,
        from_ts=query.from_ts,
        to_ts=query.to_ts,
        limit=query.limit,
    )
    return [BarResponse(**row) for row in rows]
