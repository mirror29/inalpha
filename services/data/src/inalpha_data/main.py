"""data service FastAPI 入口。

启动：``uvicorn inalpha_data.main:app --port 8001``
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
from .api import backfill, bars, health, ticker
from .config import get_data_settings
from .connectors.binance import close_connector, init_connector

_settings = get_data_settings()
configure_logging(level=_settings.log_level, service_name=_settings.service_name)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """startup / shutdown 钩子 —— 起 DB 池 + Binance connector。"""
    await init_pool(_settings.database_url)
    init_connector(
        api_key=_settings.binance_api_key,
        api_secret=_settings.binance_api_secret,
    )
    try:
        yield
    finally:
        await close_connector()
        await close_pool()


app = FastAPI(
    title="inalpha-data",
    version=__version__,
    description="行情数据接入 / 时序存储 / 历史查询",
    lifespan=lifespan,
)
install_request_logging(app)
install_error_handler(app)

app.include_router(health.router)
app.include_router(bars.router)
app.include_router(backfill.router)
app.include_router(ticker.router)
