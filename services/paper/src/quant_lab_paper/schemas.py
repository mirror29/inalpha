"""REST API 请求 / 响应 schema。"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator


def _assume_utc_if_naive(v: datetime) -> datetime:
    return v.replace(tzinfo=UTC) if v.tzinfo is None else v


class BacktestRequest(BaseModel):
    """``POST /backtest`` 请求体。"""

    strategy_id: str = Field(
        ...,
        description="已注册的策略 ID，目前仅支持 'sma_cross'",
        examples=["sma_cross"],
    )
    params: dict[str, Any] = Field(
        default_factory=dict,
        description="策略参数 dict；SMA cross 支持 fast_period / slow_period / trade_size",
        examples=[{"fast_period": 10, "slow_period": 30, "trade_size": 0.01}],
    )

    venue: str = Field(default="binance")
    symbol: str = Field(..., examples=["BTC/USDT"])
    timeframe: str = Field(default="1h", examples=["1m", "5m", "1h", "1d"])
    from_ts: datetime = Field(..., description="起始时间（含），ISO 8601")
    to_ts: datetime = Field(..., description="结束时间（含），ISO 8601")

    initial_cash: float = Field(default=10_000.0, gt=0)
    fee_rate: float = Field(default=0.001, ge=0, lt=1)

    @field_validator("from_ts", "to_ts", mode="after")
    @classmethod
    def _ensure_aware(cls, v: datetime) -> datetime:
        return _assume_utc_if_naive(v)


class PositionSnapshot(BaseModel):
    instrument_id: str  # "BTC/USDT@binance"
    quantity: float
    avg_open_price: float
    realized_pnl: float
    generation: int


class BacktestResponse(BaseModel):
    """``POST /backtest`` 响应。"""

    strategy_id: str
    venue: str
    symbol: str
    timeframe: str

    initial_cash: float
    final_equity: float
    total_return_pct: float

    num_trades: int
    total_fees: float
    num_bars_processed: int

    period_start: datetime
    period_end: datetime

    final_positions: list[PositionSnapshot]


class HealthResponse(BaseModel):
    status: str = "ok"
    service: str
    version: str
