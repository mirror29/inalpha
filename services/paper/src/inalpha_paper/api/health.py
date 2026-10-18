"""存活探活 —— 不需要 auth。"""
from __future__ import annotations

from fastapi import APIRouter

from .. import __version__
from ..schemas import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """liveness。

    paper 的业务数据（accounts / orders / positions / runs / plans / wallets）都在
    PostgreSQL，但本端点**刻意只做进程探活**：不连 DB、不反映依赖健康，DB 不可用时
    仍会返回 200。需要依赖健康时另做 readiness 检查。
    """
    return HealthResponse(status="ok", service="paper", version=__version__)
