"""research service FastAPI 入口。

启动：``uvicorn inalpha_research.main:app --port 8003``
"""

from __future__ import annotations

import asyncio
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI
from inalpha_shared import (
    configure_logging,
    install_error_handler,
    install_request_logging,
)

from . import __version__
from .api import deep_dive, event_facts, health
from .config import get_research_settings
from .event_worker import run_extraction_worker

_settings = get_research_settings()
configure_logging(level=_settings.log_level, service_name=_settings.service_name)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Start the optional durable event-extraction worker without owning Data state."""
    stop = asyncio.Event()
    task: asyncio.Task[None] | None = None
    if _settings.event_extraction_enabled:
        worker_id = f"research:{socket.gethostname()}:{uuid4().hex[:12]}"
        task = asyncio.create_task(
            run_extraction_worker(_settings, stop, worker_id=worker_id),
            name="event-extraction-worker",
        )
    try:
        yield
    finally:
        stop.set()
        if task is not None:
            await task


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
app.include_router(event_facts.router)
