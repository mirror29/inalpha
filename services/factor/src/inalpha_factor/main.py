"""factor service FastAPI 入口。

启动：``uvicorn inalpha_factor.main:app --port 8004``

DB 是**可选**的（D-12 起）：连上 → candidates 候选池 + custom 注册表可用；
连不上 → candidates 路由 503，timing/score/catalog 照常（因子仍按需从
data-service 拉 OHLCV 现算）。
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from inalpha_shared import (
    configure_logging,
    install_error_handler,
    install_request_logging,
)
from inalpha_shared.db import close_pool, init_pool

from . import __version__, custom_registry
from .api import (
    candidates,
    catalog,
    compute,
    custom,
    health,
    panel,
    score,
    snapshot,
)
from .config import get_factor_settings

_settings = get_factor_settings()
configure_logging(level=_settings.log_level, service_name=_settings.service_name)
_logger = logging.getLogger(__name__)

#: custom 注册表后台刷新周期（review 后另有立即刷新，这里只兜"别的进程改了 DB"）
_REGISTRY_REFRESH_S = 60.0


async def _registry_refresh_loop() -> None:
    while True:
        await asyncio.sleep(_REGISTRY_REFRESH_S)
        await custom_registry.refresh()  # 失败自己 log + 保留旧缓存，不抛


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """DB best-effort 接入：失败只降级 candidates 路由，绝不阻塞服务起步。"""
    app.state.db_ready = False
    refresh_task: asyncio.Task[None] | None = None
    try:
        await init_pool(_settings.database_url)
        app.state.db_ready = True
        n = await custom_registry.refresh()
        _logger.info("factor DB 已连接；custom 注册表加载 %d 个已注册因子", n)
        refresh_task = asyncio.create_task(
            _registry_refresh_loop(), name="custom-registry-refresh"
        )
    except Exception as exc:
        _logger.warning(
            "factor DB 不可用（candidates 路由将 503，timing/score/catalog 不受影响）: %r",
            exc,
        )
    try:
        yield
    finally:
        if refresh_task is not None and not refresh_task.done():
            refresh_task.cancel()
            try:
                await refresh_task
            except (asyncio.CancelledError, Exception):
                pass
        if app.state.db_ready:
            await close_pool()


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
app.include_router(panel.router)
app.include_router(custom.router)
app.include_router(candidates.router)
