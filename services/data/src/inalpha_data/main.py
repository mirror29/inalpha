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
from .api import (
    backfill,
    bars,
    constituents,
    fundamentals,
    fx,
    health,
    market,
    news,
    perp,
    symbols,
    ticker,
    web_fetch,
    web_search,
)
from .config import get_data_settings
from .connectors import akshare as akshare_conn
from .connectors import alpaca as alpaca_conn
from .connectors import binance as binance_conn
from .connectors import cn_market as cn_market_conn
from .connectors import fred as fred_conn
from .connectors import symbol_search as symbol_search_conn
from .connectors import web_fetch as web_fetch_conn
from .connectors import web_search as web_search_conn
from .connectors import yfinance_conn
from .scheduler import ConstituentSnapshotScheduler, parse_indices

_settings = get_data_settings()
configure_logging(level=_settings.log_level, service_name=_settings.service_name)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """startup / shutdown 钩子 —— 起 DB 池 + 三个 venue connector。

    每个 connector 都登记到 ``connectors._base`` 注册表，由 ``api/backfill.py``
    按 ``req.venue`` 取用。关停时反向 close 三个。
    """
    await init_pool(_settings.database_url)
    binance_conn.init_connector(
        api_key=_settings.binance_api_key,
        api_secret=_settings.binance_api_secret,
    )
    alpaca_conn.init_connector(
        api_key=_settings.alpaca_api_key,
        api_secret=_settings.alpaca_api_secret,
    )
    akshare_conn.init_connector()
    yfinance_conn.init_connector()
    fred_conn.init_connector(api_key=_settings.fred_api_key)
    web_search_conn.init_connector()
    cn_market_conn.init_connector()
    web_fetch_conn.init_connector()
    symbol_search_conn.init_connector()
    # 成分快照每日调度（ADR-0053 阶段 C 向前累积）——无追踪指数则自动禁用
    snapshot_scheduler = ConstituentSnapshotScheduler(
        index_codes=parse_indices(_settings.constituent_snapshot_indices),
        interval_s=_settings.constituent_snapshot_interval_h * 3600,
    )
    snapshot_scheduler.start()
    try:
        yield
    finally:
        await snapshot_scheduler.stop()
        await symbol_search_conn.close_connector()
        await web_fetch_conn.close_connector()
        await fred_conn.close_connector()
        await yfinance_conn.close_connector()
        await cn_market_conn.close_connector()
        await web_search_conn.close_connector()
        await akshare_conn.close_connector()
        await alpaca_conn.close_connector()
        await binance_conn.close_connector()
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
app.include_router(news.router)
app.include_router(market.router)
app.include_router(constituents.router)
app.include_router(fundamentals.router)
app.include_router(fx.router)
app.include_router(perp.router)
app.include_router(web_search.router)
app.include_router(web_fetch.router)
app.include_router(symbols.router)
