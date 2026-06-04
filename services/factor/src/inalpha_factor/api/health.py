"""存活探活 —— 不需要 auth。"""
from __future__ import annotations

from fastapi import APIRouter

from .. import __version__
from ..deps import EngineDep, SettingsDep
from ..schemas import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health(engine: EngineDep, settings: SettingsDep) -> HealthResponse:
    """liveness + 各因子源可用性（便于调测看 qlib 是否启用）。"""
    return HealthResponse(
        status="ok",
        service="factor",
        version=__version__,
        qlib_enabled=settings.qlib_enabled,
        adapters=engine.sources(),
    )
