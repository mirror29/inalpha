"""请求 / 响应 schema —— 给 FastAPI 路由用，自动生成 OpenAPI。"""
from __future__ import annotations

from datetime import UTC, datetime

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


class TickerQuery(BaseModel):
    """``GET /ticker`` 的 query 参数。"""

    venue: str = Field(default="binance", description="交易所标识")
    symbol: str = Field(..., examples=["BTC/USDT"])


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
