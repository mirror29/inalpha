"""``POST /backfill/bars`` —— 从外部交易所拉历史 K 线落库。"""
from __future__ import annotations

from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends
from quant_lab_shared import get_logger
from quant_lab_shared.auth import User, get_current_user
from quant_lab_shared.db import DBConn
from quant_lab_shared.errors import ValidationError

from ..connectors.binance import TIMEFRAME_SECONDS, BinanceConnector, get_connector
from ..schemas import BackfillRequest, BackfillResponse
from ..storage.bars import insert_bars

router = APIRouter(tags=["backfill"])
_logger = get_logger(__name__)

# 单次 fetch 上限（Binance 默认 500，可调到 1000）
_BATCH_LIMIT = 1000


@router.post("/backfill/bars", response_model=BackfillResponse)
async def backfill_bars(
    req: BackfillRequest,
    db: DBConn,
    connector: Annotated[BinanceConnector, Depends(get_connector)],
    _user: Annotated[User, Depends(get_current_user)],
) -> BackfillResponse:
    """从 Binance 拉指定时段的 K 线，幂等写入 TimescaleDB。

    实现：分批 ``fetch_ohlcv`` + ``executemany`` ON CONFLICT DO UPDATE。
    """
    if req.from_ts > req.to_ts:
        raise ValidationError("from_ts must be <= to_ts")
    if req.timeframe not in TIMEFRAME_SECONDS:
        raise ValidationError(
            f"unsupported timeframe: {req.timeframe}",
            details={"supported": list(TIMEFRAME_SECONDS.keys())},
        )
    if req.venue != "binance":
        raise ValidationError(
            f"only binance is supported in MVP, got {req.venue!r}",
            details={"venue": req.venue},
        )

    tf_seconds = TIMEFRAME_SECONDS[req.timeframe]
    cursor = req.from_ts
    fetched_total = 0
    inserted_total = 0

    while cursor < req.to_ts:
        bars = await connector.fetch_bars(
            symbol=req.symbol,
            timeframe=req.timeframe,
            since=cursor,
            limit=_BATCH_LIMIT,
        )
        if not bars:
            _logger.info(
                "backfill_no_more_bars",
                symbol=req.symbol,
                cursor=cursor.isoformat(),
            )
            break

        # 过滤掉超过 to_ts 的 bar
        bars = [b for b in bars if b[0] <= req.to_ts]
        if not bars:
            break

        n = await insert_bars(db, "binance", req.symbol, req.timeframe, bars)
        fetched_total += len(bars)
        inserted_total += n

        last_ts = bars[-1][0]
        next_cursor = last_ts + timedelta(seconds=tf_seconds)
        if next_cursor <= cursor:
            # 兜底：游标没推进就停，防止死循环（理论上不会发生）
            _logger.warning(
                "backfill_cursor_stuck",
                cursor=cursor.isoformat(),
                last_ts=last_ts.isoformat(),
            )
            break
        cursor = next_cursor

    _logger.info(
        "backfill_done",
        symbol=req.symbol,
        timeframe=req.timeframe,
        fetched=fetched_total,
        inserted=inserted_total,
    )

    return BackfillResponse(
        venue=req.venue,
        symbol=req.symbol,
        timeframe=req.timeframe,
        bars_fetched=fetched_total,
        bars_inserted=inserted_total,
        from_ts=req.from_ts,
        to_ts=req.to_ts,
    )
