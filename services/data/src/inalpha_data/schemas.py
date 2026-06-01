"""请求 / 响应 schema —— 给 FastAPI 路由用，自动生成 OpenAPI。"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator


def _assume_utc_if_naive(v: datetime) -> datetime:
    """把 naive datetime 当作 UTC 处理，避免 TIMESTAMPTZ 字段插入歧义。"""
    return v.replace(tzinfo=UTC) if v.tzinfo is None else v


class BarsQuery(BaseModel):
    """``GET /bars`` 的 query 参数。"""

    venue: str = Field(default="binance", description="交易所标识")
    symbol: str = Field(..., examples=["BTC/USDT"], description="交易对，含 /")
    timeframe: str = Field(default="1h", examples=["1m", "5m", "1h", "1d"])
    from_ts: datetime = Field(..., description="起始时间（含），ISO 8601")
    to_ts: datetime = Field(..., description="结束时间（含），ISO 8601")
    limit: int = Field(default=10000, ge=1, le=50000)

    @field_validator("from_ts", "to_ts", mode="after")
    @classmethod
    def _ensure_aware(cls, v: datetime) -> datetime:
        return _assume_utc_if_naive(v)


class BarResponse(BaseModel):
    """单根 K 线。"""

    ts: datetime
    venue: str
    symbol: str
    timeframe: str
    open: float
    high: float
    low: float
    close: float
    volume: float


class BackfillRequest(BaseModel):
    """``POST /backfill/bars`` 请求体。"""

    venue: str = Field(default="binance")
    symbol: str = Field(..., examples=["BTC/USDT"])
    timeframe: str = Field(default="1h")
    from_ts: datetime
    to_ts: datetime

    @field_validator("from_ts", "to_ts", mode="after")
    @classmethod
    def _ensure_aware(cls, v: datetime) -> datetime:
        return _assume_utc_if_naive(v)


class BackfillResponse(BaseModel):
    """``POST /backfill/bars`` 响应。"""

    venue: str
    symbol: str
    timeframe: str
    bars_fetched: int
    bars_inserted: int
    from_ts: datetime
    to_ts: datetime


class HealthResponse(BaseModel):
    status: str = "ok"
    service: str
    version: str
    db: str = "ok"


# ────────────────────────────────────────────────────────────────────
# Ticker（D-8a' 加：给 paper /orders/submit 服务端取 refPrice 用）
# ────────────────────────────────────────────────────────────────────


# ────────────────────────────────────────────────────────────────────
# News（D-9 加：给 research macro/sentiment analyst 喂真新闻）
# ────────────────────────────────────────────────────────────────────


class NewsQuery(BaseModel):
    """``GET /news`` 的 query 参数。"""

    venue: str = Field(
        default="yfinance",
        description="新闻数据源 venue。支持 yfinance（全球零 key）和 akshare（A股）。",
    )
    symbol: str = Field(
        ...,
        examples=["AAPL", "^GSPC", "005930.KS", "sh.600519"],
        description="ticker 标识：yfinance 用 Yahoo ticker，akshare 用 sh./sz. 前缀。",
    )
    limit: int = Field(default=10, ge=1, le=30, description="最多返回多少条")


class NewsItem(BaseModel):
    """单条新闻头条。"""

    title: str
    publisher: str = ""
    link: str = ""
    published_at: datetime | None = None
    summary: str = ""


class NewsResponse(BaseModel):
    """``GET /news`` 响应：按发布时间倒序（最新在 items[0]）。"""

    venue: str
    symbol: str
    items: list[NewsItem]


class TickerQuery(BaseModel):
    """``GET /ticker`` 的 query 参数。"""

    venue: str = Field(default="binance", description="交易所标识")
    symbol: str = Field(..., examples=["BTC/USDT"])
    fresh: bool = Field(
        default=False,
        description=(
            "true 时绕过 DB 缓存，直接调外部市场实时 ticker。"
            "支持 venue：binance / yfinance / alpaca；akshare / fred 不支持，"
            "会返 422 FRESH_NOT_SUPPORTED_FOR_VENUE 并提示切 fresh=false。"
            "适合 scheduler 周期性拉真·最新价的场景。"
        ),
    )


class TickerResponse(BaseModel):
    """单次最新价。

    取值优先级：
    1. DB 里最新一根 1m bar.close（若 stale_seconds < 阈值）
    2. fallback：DB 里最新一根 1h bar.close
    3. 都没有 → 返回 404 NO_PRICE_AVAILABLE（让 caller 决定 backfill 还是失败）
    """

    venue: str
    symbol: str
    price: float
    ts: datetime
    source: str = Field(description="数据源：'db_1m' | 'db_1h' | 'binance_ticker'（未来）")
    is_stale: bool = Field(description="距离当前是否超过 5 分钟")
    stale_seconds: int = Field(description="数据相对 now 的秒数")


# ────────────────────────────────────────────────────────────────────
# Financials（D-10 加：财报基本面数据 fetching，给 research analyst 用）
# ────────────────────────────────────────────────────────────────────


class FinancialsIndicator(BaseModel):
    """标准化财报指标 —— 任一字段缺失时为 None。"""

    market_cap: float | None = None
    pe_ratio: float | None = None
    pb_ratio: float | None = None
    roe: float | None = None
    revenue_yoy: float | None = None
    profit_yoy: float | None = None
    gross_margin: float | None = None
    net_margin: float | None = None
    debt_to_equity: float | None = None


class FinancialsResponse(BaseModel):
    """``GET /fundamentals`` 响应：标准化财报基本面数据。"""

    venue: str
    symbol: str
    available: bool = True
    reason: str | None = None
    as_of: str | None = None
    indicators: FinancialsIndicator = Field(default_factory=FinancialsIndicator)
    raw: dict[str, Any] | None = None


# ────────────────────────────────────────────────────────────────────
# Web search（D-9 加：ddgs metasearch，零 key 搜索端）
# ────────────────────────────────────────────────────────────────────


class WebSearchResult(BaseModel):
    title: str
    url: str
    snippet: str = ""


class WebSearchResponse(BaseModel):
    query: str
    backend: str
    results: list[WebSearchResult] = Field(default_factory=list)
