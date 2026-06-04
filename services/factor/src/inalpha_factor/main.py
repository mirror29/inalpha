"""factor service FastAPI 入口。

启动：``uvicorn inalpha_factor.main:app --port 8004``

不连 DB（因子按需从 data-service 拉 OHLCV 现算；有效性缓存留作后续 factor_cache 迁移）。
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
from .api import catalog, compute, health, score, snapshot
from .config import get_factor_settings

_settings = get_factor_settings()
configure_logging(level=_settings.log_level, service_name=_settings.service_name)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """无 DB / 无外部连接预热——每请求新建 DataClient。"""
    yield


app = FastAPI(
    title="inalpha-factor",
    version=__version__,
    description="接现成因子库（pandas-ta / Alpha101 / qlib Alpha158）+ 有效性打分（前瞻收益 / Rank IC）",
    lifespan=lifespan,
)
install_request_logging(app)
install_error_handler(app)

app.include_router(health.router)
app.include_router(catalog.router)
app.include_router(compute.router)
app.include_router(score.router)
app.include_router(snapshot.router)
