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
