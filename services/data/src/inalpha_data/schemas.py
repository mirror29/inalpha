"""请求 / 响应 schema —— 给 FastAPI 路由用，自动生成 OpenAPI。"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

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
# FX（D-11 加：汇率查询，给 paper 跨币种 cash / equity 折算用）
# ────────────────────────────────────────────────────────────────────


class FxQuery(BaseModel):
    """``GET /fx`` 的 query 参数。``rate`` 语义 = 1 单位 ``base`` 值多少 ``quote``。"""

    base: str = Field(..., examples=["CNY", "JPY", "USD"], description="源货币 code")
    quote: str = Field(default="USD", examples=["USD"], description="目标货币 code")


class FxResponse(BaseModel):
    """``GET /fx`` 响应。``rate`` = 1 ``base`` 折算成多少 ``quote``。

    取值优先级：
    1. ``base == quote`` → 1.0（``source='identity'``）
    2. 两边都是 USD 等价稳定币（USD / USDT / USDC）→ 1.0（``source='stablecoin'``）
    3. yfinance forex pair（``{base}{quote}=X``）实时价（``source='yfinance'``）

    yfinance 拿不到时抛 ``FX_UNAVAILABLE``（502），由 caller 决定降级（paper equity
    折算遇此会把该币种排除并显式 warning，不静默用旧值 / 不乱猜汇率）。
    """

    base: str
    quote: str
    rate: float
    ts: datetime
    source: str = Field(description="'identity' | 'stablecoin' | 'yfinance'")
    is_stale: bool = Field(description="距当前是否超过新鲜阈值（FX 放宽到 ~1h）")
    stale_seconds: int


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


#: 搜索结果状态的单一真相源——connector 的 ``SearchOutcome.status`` 与
#: ``_classify_exception`` 也复用它，避免两处 Literal 漂移（新增状态只加一处忘了
#: 另一处会让 Pydantic 在 API 层 runtime 抛 ValidationError → 搜索端点 500）。
WebSearchStatus = Literal["ok", "no_results", "timeout", "rate_limited", "engine_error"]


class WebSearchResponse(BaseModel):
    """搜索结果 + 失败原因透传。

    status 区分"真没搜到"（no_results，可当弱证据）与"引擎故障"
    （timeout / rate_limited / engine_error，不能当"无证据"用）——
    修复前两者都被静默吞成空数组，agent 无法降级。
    """

    query: str
    backend: str
    """实际使用的搜索引擎（含 fallback / 降级后），不一定等于请求参数。"""
    status: WebSearchStatus = "ok"
    error: str | None = None
    hint: str | None = None
    """给 agent 的下一步建议（如中文 news 降级后指向市场级快讯工具）。"""
    fetched_at: str | None = None
    results: list[WebSearchResult] = Field(default_factory=list)


# ────────────────────────────────────────────────────────────────────
# CN market（D-12+ 行情归因：A股市场级快讯/板块/资金/强势股，无需 symbol）
# ────────────────────────────────────────────────────────────────────


class MarketNewsItem(BaseModel):
    title: str
    summary: str = ""
    published_at: str | None = None
    """UTC ISO 字符串（源站北京时间已转换）。"""
    related_codes: list[str] = Field(default_factory=list)


class MarketNewsResponse(BaseModel):
    market: str
    source: str = "eastmoney"
    fetched_at: datetime
    items: list[MarketNewsItem] = Field(default_factory=list)


class SectorBoardItem(BaseModel):
    name: str
    code: str
    pct_chg: float | None = None
    """当日涨跌幅（百分数，如 3.5 表示 +3.5%）。"""
    up_count: int | None = None
    down_count: int | None = None
    leader: str = ""
    leader_code: str = ""
    leader_pct_chg: float | None = None


class SectorBoardResponse(BaseModel):
    market: str
    fetched_at: datetime
    total_boards: int = 0
    top: list[SectorBoardItem] = Field(default_factory=list)
    bottom: list[SectorBoardItem] = Field(default_factory=list)


class MoneyflowPoint(BaseModel):
    time: str
    """北京时间 HH:MM。"""
    hgt: float | None = None
    sgt: float | None = None


class MoneyflowResponse(BaseModel):
    market: str
    fetched_at: datetime
    as_of_time: str | None = None
    """最后一个有数的分钟点（北京时间 HH:MM）。"""
    hgt_net_yi_cny: float | None = None
    sgt_net_yi_cny: float | None = None
    north_net_yi_cny: float | None = None
    series_sample: list[MoneyflowPoint] = Field(default_factory=list)
    note: str = ""


class StrongStockItem(BaseModel):
    code: str
    name: str
    reason: str = ""
    """人工题材标签原文（"+"分隔多个题材）。"""
    tags: list[str] = Field(default_factory=list)
    date: str = ""


class StrongStocksResponse(BaseModel):
    market: str
    fetched_at: datetime
    items: list[StrongStockItem] = Field(default_factory=list)


# ────────────────────────────────────────────────────────────────────
# Web fetch（证据链：URL → 可引用的正文文本）
# ────────────────────────────────────────────────────────────────────


class WebFetchResponse(BaseModel):
    url: str
    final_url: str | None = None
    title: str | None = None
    published_at: str | None = None
    """trafilatura 从页面元数据抽的发布日期（ISO 字符串）；抽不到为 None。"""
    text: str = ""
    truncated: bool = False
    fetched_at: str | None = None
    error: str | None = None


# ────────────────────────────────────────────────────────────────────
# Symbol search（公司名 / 关键词 → ticker 解析）
# ────────────────────────────────────────────────────────────────────


class SymbolSearchResult(BaseModel):
    symbol: str
    """已按 venue 约定格式化：akshare → ``sh.600519``；yfinance → ``AAPL`` / ``0700.HK``。"""
    name: str = ""
    exchange: str = ""
    venue: str
    quote_type: str = ""


class SymbolSearchResponse(BaseModel):
    query: str
    results: list[SymbolSearchResult] = Field(default_factory=list)


# ── 指数成分 PIT 快照（#106 / ADR-0053 阶段 C）────────────────────────


class ConstituentItem(BaseModel):
    code: str
    """成分符号，Inalpha 格式（sh./sz./bj. 前缀）。"""
    name: str | None = None
    weight: float | None = None


class SnapshotConstituentsRequest(BaseModel):
    index_code: str = Field(
        ..., examples=["000300"], description="中证/常用指数代码（000300 沪深300 等）"
    )


class SnapshotConstituentsResponse(BaseModel):
    index_code: str
    as_of_date: str
    """快照日（今天）。"""
    count: int


class ConstituentsResponse(BaseModel):
    index_code: str
    as_of: str
    """请求的 PIT 时点。"""
    snapshot_date: str | None = None
    """实际命中的快照日（as_of_date <= as_of 的最近一份）；None = 无可用快照。"""
    is_pit: bool = Field(
        description="是否命中 PIT 快照。false = as_of 早于最早快照（向前累积尚未覆盖），"
        "成分不可信、带存活者偏差，调用方须显式降级（§3.1）"
    )
    reason: str | None = None
    constituents: list[ConstituentItem] = Field(default_factory=list)
