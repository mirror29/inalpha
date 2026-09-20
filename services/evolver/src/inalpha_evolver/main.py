"""Evolver FastAPI 应用入口。

使用方式：:

    uvicorn inalpha_evolver.main:app --port 8005 --reload
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from inalpha_shared.db import close_pool, init_pool
from inalpha_shared.middleware import install_error_handler, install_request_logging

from .api.routes import router
from .config import get_evolver_settings
from .runtime import EvolutionRunManager
from .runtime.campaign_manager import CampaignManager
from .runtime.loop_manager import LoopManager

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """初始化 DB 队列与 manager；E1 强制单 API worker。"""
    settings = get_evolver_settings()
    workers = int(os.environ.get("WEB_CONCURRENCY", os.environ.get("WORKERS", "1")))
    if workers != 1:
        raise RuntimeError("evolver requires exactly one API worker in E1")
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL is required for evolver")
    await init_pool(
        settings.database_url,
        min_size=2,
        max_size=settings.evolver_pool_size,
    )
    manager = EvolutionRunManager(mutator=None, settings=settings)
    app.state.evolution_manager = manager
    await manager.start()
    campaign_manager = CampaignManager(settings) if settings.event_evolution_enabled else None
    app.state.campaign_manager = campaign_manager
    if campaign_manager is not None:
        await campaign_manager.start()
    loop_manager = LoopManager(settings, campaign_manager) if settings.event_evolution_enabled else None
    app.state.loop_manager = loop_manager
    if loop_manager is not None:
        await loop_manager.start()
    try:
        yield
    finally:
        if loop_manager is not None:
            await loop_manager.close()
        if campaign_manager is not None:
            await campaign_manager.close()
        await manager.close()
        await close_pool()


app = FastAPI(
    title="Inalpha Evolver API",
    description="策略演化引擎 —— LLM-as-mutation-operator 闭环",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(router)
install_request_logging(app)
install_error_handler(app)


@app.get("/health", response_model=None)
async def health(request: Request) -> dict[str, str] | JSONResponse:
    manager = getattr(request.app.state, "evolution_manager", None)
    if manager is None or not manager.healthy:
        reason = getattr(manager, "unhealthy_reason", None) or "dispatcher unavailable"
        return JSONResponse(
            status_code=503,
            content={
                "status": "unhealthy",
                "service": "inalpha-evolver",
                "reason": reason,
            },
        )
    campaign_manager = getattr(request.app.state, "campaign_manager", None)
    if campaign_manager is not None and not campaign_manager.healthy:
        return JSONResponse(
            status_code=503,
            content={
                "status": "unhealthy",
                "service": "inalpha-evolver",
                "reason": campaign_manager.unhealthy_reason or "campaign dispatcher unavailable",
            },
        )
    response = {"status": "ok", "service": "inalpha-evolver"}
    loop_manager = getattr(request.app.state, "loop_manager", None)
    if loop_manager is not None and not loop_manager.healthy:
        return JSONResponse(status_code=503, content={
            "status": "unhealthy", "service": "inalpha-evolver",
            "reason": loop_manager.unhealthy_reason or "loop dispatcher unavailable",
        })
    if campaign_manager is not None:
        response["event_evolution"] = "enabled"
    return response
