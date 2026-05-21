"""paper service FastAPI 入口。

启动：``uvicorn quant_lab_paper.main:app --port 8002``
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from quant_lab_shared import (
    configure_logging,
    install_error_handler,
    install_request_logging,
)

from . import __version__
from .api import backtest, health
from .config import get_paper_settings

_settings = get_paper_settings()
configure_logging(level=_settings.log_level, service_name=_settings.service_name)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """D-6 不连 DB，lifespan 暂时是 noop。D-7 持久化时加 init_pool。"""
    yield


app = FastAPI(
    title="quant-lab-paper",
    version=__version__,
    description="回测 / 模拟盘 / 实盘三合一引擎",
    lifespan=lifespan,
)
install_request_logging(app)
install_error_handler(app)

app.include_router(health.router)
app.include_router(backtest.router)
