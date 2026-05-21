"""存活探活 —— 不需要 auth。"""
from __future__ import annotations

from fastapi import APIRouter

from .. import __version__
from ..schemas import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """liveness。

    paper service D-6 不直接连 DB（只调 data-service），所以不做 DB ping。
    D-7 持久化 backtest_runs 之后会加 DB ping。
    """
    return HealthResponse(status="ok", service="paper", version=__version__)
