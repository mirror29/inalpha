"""Evolver FastAPI 应用入口。

使用方式：:

    uvicorn inalpha_evolver.main:app --port 8003 --reload
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .api.routes import router
from .config import get_evolver_settings

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """服务生命周期管理。

    E1 暂不连接 DB（使用内存存储）。E2 启 DB pool + LLM client。
    """
    settings = get_evolver_settings()
    logger.info(
        "Evolver 服务启动 (model=%s, timeout=%ds)",
        settings.llm_model,
        settings.evolver_job_timeout_s,
    )
    yield
    logger.info("Evolver 服务关闭")


app = FastAPI(
    title="Inalpha Evolver API",
    description="策略演化引擎 —— LLM-as-mutation-operator 闭环",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "inalpha-evolver"}