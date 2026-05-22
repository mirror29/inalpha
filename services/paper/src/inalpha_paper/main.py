"""paper service FastAPI 入口。

启动：``uvicorn inalpha_paper.main:app --port 8002``
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from inalpha_shared import (
    close_pool,
    configure_logging,
    init_pool,
    install_error_handler,
    install_request_logging,
)

from . import __version__
from .api import backtest, health, orders, strategies, trade_plans
from .config import get_paper_settings

_settings = get_paper_settings()
configure_logging(level=_settings.log_level, service_name=_settings.service_name)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """D-8b 起连 DB pool —— 持久化 orders / positions / trade_plans / accounts。"""
    await init_pool(_settings.database_url)
    try:
        yield
    finally:
        await close_pool()


app = FastAPI(
    title="inalpha-paper",
    version=__version__,
    description="回测 / 模拟盘 / 实盘三合一引擎",
    lifespan=lifespan,
)
install_request_logging(app)
install_error_handler(app)

app.include_router(health.router)
app.include_router(backtest.router)
app.include_router(orders.router)
app.include_router(strategies.router)
app.include_router(trade_plans.router)
