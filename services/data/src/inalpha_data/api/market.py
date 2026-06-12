"""``GET /market/*`` —— 市场级行情归因数据（D-12+，无需 symbol）。

行情归因（"今天为什么涨/跌"）的四个数据维度：
- ``/market/news``      全市场财经快讯
- ``/market/sectors``   行业板块涨跌幅榜（普涨 vs 结构性）
- ``/market/moneyflow`` 沪深港通资金流向
- ``/market/movers``    当日强势股 + 人工题材标签

venue 按 ``market`` 路由（同 ``/news`` 的 venue 模式）：当前实装 ``cn``（A股，
直连东财/同花顺，配方源自 a-stock-data）；其它市场返 400 MARKET_NOT_SUPPORTED，
将来扩展只改 ``_resolve``。

失败语义：源站失败（反爬/改版/网络）→ 502 MARKET_DATA_UNAVAILABLE，**不**静默
返空——市场级数据是归因的结论级输入，"故障"被吞成"无数据"会让 agent 把
残缺归因当完整结论输出。
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from inalpha_shared import get_logger
from inalpha_shared.auth import User, get_current_user
from inalpha_shared.errors import InalphaError, ValidationError

from ..connectors.cn_market import CnMarketConnector, CnMarketError, get_connector
from ..schemas import (
    MarketNewsResponse,
    MoneyflowResponse,
    SectorBoardResponse,
    StrongStocksResponse,
)

_logger = get_logger(__name__)
router = APIRouter(tags=["market"])

_SUPPORTED_MARKETS = ("cn",)


class MarketDataUnavailableError(InalphaError):
    code = "MARKET_DATA_UNAVAILABLE"
    status_code = 502


def _resolve(market: str) -> CnMarketConnector:
    """market → connector。将来加 us/hk 等市场只改这里。"""
    if market == "cn":
        return get_connector()
    raise ValidationError(
        f"market {market!r} not supported",
        code="MARKET_NOT_SUPPORTED",
        details={"market": market, "supported": list(_SUPPORTED_MARKETS)},
    )


@router.get("/market/news", response_model=MarketNewsResponse)
async def market_news(
    _user: Annotated[User, Depends(get_current_user)],
    market: Annotated[str, Query(description="市场，当前支持 cn")] = "cn",
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> MarketNewsResponse:
    """全市场财经快讯（东财 7×24），无需 symbol。"""
    conn = _resolve(market)
    try:
        items = await conn.fetch_market_news(limit=limit)
    except CnMarketError as exc:
        _logger.warning("market_news_failed", market=market, error=str(exc))
        raise MarketDataUnavailableError(str(exc)) from exc
    return MarketNewsResponse(
        market=market, fetched_at=datetime.now(UTC), items=items  # type: ignore[arg-type]
    )


@router.get("/market/sectors", response_model=SectorBoardResponse)
async def market_sectors(
    _user: Annotated[User, Depends(get_current_user)],
    market: Annotated[str, Query(description="市场，当前支持 cn")] = "cn",
    top_n: Annotated[int, Query(ge=1, le=50)] = 10,
) -> SectorBoardResponse:
    """行业板块涨跌幅榜（top/bottom 两端）。"""
    conn = _resolve(market)
    try:
        board = await conn.fetch_sector_board(top_n=top_n)
    except CnMarketError as exc:
        _logger.warning("market_sectors_failed", market=market, error=str(exc))
        raise MarketDataUnavailableError(str(exc)) from exc
    return SectorBoardResponse(
        market=market,
        fetched_at=datetime.now(UTC),
        total_boards=board["total_boards"],
        top=board["top"],
        bottom=board["bottom"],
    )


@router.get("/market/moneyflow", response_model=MoneyflowResponse)
async def market_moneyflow(
    _user: Annotated[User, Depends(get_current_user)],
    market: Annotated[str, Query(description="市场，当前支持 cn")] = "cn",
) -> MoneyflowResponse:
    """沪深港通资金分钟流向（同花顺估算口径）。"""
    conn = _resolve(market)
    try:
        flow = await conn.fetch_moneyflow()
    except CnMarketError as exc:
        _logger.warning("market_moneyflow_failed", market=market, error=str(exc))
        raise MarketDataUnavailableError(str(exc)) from exc
    return MoneyflowResponse(market=market, fetched_at=datetime.now(UTC), **flow)


@router.get("/market/movers", response_model=StrongStocksResponse)
async def market_movers(
    _user: Annotated[User, Depends(get_current_user)],
    market: Annotated[str, Query(description="市场，当前支持 cn")] = "cn",
    limit: Annotated[int, Query(ge=1, le=50)] = 30,
) -> StrongStocksResponse:
    """当日强势股 + 人工题材标签（归因"什么主线在涨"）。"""
    conn = _resolve(market)
    try:
        items = await conn.fetch_strong_stocks(limit=limit)
    except CnMarketError as exc:
        _logger.warning("market_movers_failed", market=market, error=str(exc))
        raise MarketDataUnavailableError(str(exc)) from exc
    return StrongStocksResponse(
        market=market, fetched_at=datetime.now(UTC), items=items  # type: ignore[arg-type]
    )
