"""存活探活 —— 不需要 auth。"""
from __future__ import annotations

from fastapi import APIRouter
from inalpha_shared.db import DBConn

from .. import __version__
from ..schemas import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health(db: DBConn) -> HealthResponse:
    """liveness + DB ping。"""
    db_status = "ok"
    try:
        async with db.cursor() as cur:
            await cur.execute("SELECT 1 AS ok")
            await cur.fetchone()
    except Exception:
        db_status = "error"

    return HealthResponse(
        status="ok",
        service="data",
        version=__version__,
        db=db_status,
    )
