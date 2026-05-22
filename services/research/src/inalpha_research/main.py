"""research service FastAPI 入口。

启动：``uvicorn inalpha_research.main:app --port 8003``
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from inalpha_shared import (
    configure_logging,
    install_error_handler,
    install_request_logging,
)

from . import __version__
from .api import deep_dive, health
from .config import get_research_settings

_settings = get_research_settings()
configure_logging(level=_settings.log_level, service_name=_settings.service_name)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """D-8b 不连 DB。D-9+ 加 LLM 调用统计 / 缓存时再起 lifespan 资源。"""
    yield


app = FastAPI(
    title="inalpha-research",
    version=__version__,
    description="LLM 多 agent 决策（TradingAgents 风格）",
    lifespan=lifespan,
)
install_request_logging(app)
install_error_handler(app)

app.include_router(health.router)
app.include_router(deep_dive.router)
