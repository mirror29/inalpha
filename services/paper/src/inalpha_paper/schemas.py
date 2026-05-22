"""REST API 请求 / 响应 schema。"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic_core import PydanticCustomError


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


class EquityPoint(BaseModel):
    """equity_curve 单点。``ts`` 是 ISO 8601；``equity`` 是该 bar close 后的账户权益。"""

    ts: datetime
    equity: float


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

    # ─── 绩效指标（D-7+）───
    sharpe: float | None = Field(
        default=None,
        description="年化 Sharpe；样本不足或波动率为 0 时为 null",
    )
    sortino: float | None = Field(
        default=None,
        description="年化 Sortino；样本不足或无下行时为 null",
    )
    max_drawdown_pct: float = Field(
        default=0.0,
        description="最大回撤百分比（正数）",
    )
    win_rate: float | None = Field(
        default=None,
        description="round-trip 胜率（百分比）；无 round-trip 时为 null",
    )
    equity_curve: list[EquityPoint] = Field(
        default_factory=list,
        description="每根 bar 的 (ts, equity)；前端可直接画图",
    )

    final_positions: list[PositionSnapshot]


class HealthResponse(BaseModel):
    status: str = "ok"
    service: str
    version: str


# ────────────────────────────────────────────────────────────────────
# 单笔下单（D-8a 起步，in-memory）
# ────────────────────────────────────────────────────────────────────


class SubmitOrderRequest(BaseModel):
    """``POST /orders/submit`` 请求体。

    D-8a 范围：**单笔、同步、in-memory** —— 收到请求后立即按 ``ref_price`` 撮合，
    不维持持仓 / 不写库。给 orchestration 层的 ``executeTradePlan`` tool 用。
    """

    venue: str = Field(default="binance")
    symbol: str = Field(..., examples=["BTC/USDT"])
    side: Literal["BUY", "SELL"]
    order_type: Literal["MARKET", "LIMIT"] = Field(default="MARKET", alias="type")
    quantity: float = Field(..., gt=0, examples=[0.001])
    price: float | None = Field(
        default=None,
        description="LIMIT 必填；MARKET 必须为空",
    )

    # D-8a' 起：ref_price 可省略，服务端自动调 data-service /ticker 拿最新价
    # 调用方依然可显式传（如压测 / 单元测试），不传则走服务端兜底
    ref_price: float | None = Field(
        default=None,
        gt=0,
        description="撮合参考价；省略时服务端调 data-service /ticker 自取最新价",
    )

    fee_rate: float = Field(default=0.001, ge=0, lt=1)

    @model_validator(mode="after")
    def _check_price_for_type(self) -> SubmitOrderRequest:
        # 用 PydanticCustomError 而不是 ValueError —— 后者会把 exception 对象塞进
        # error.ctx，FastAPI 的统一错误响应里 errors() 无法 JSON 序列化（trip TypeError）
        if self.order_type == "LIMIT" and self.price is None:
            raise PydanticCustomError("limit_requires_price", "LIMIT order requires price")
        if self.order_type == "MARKET" and self.price is not None:
            raise PydanticCustomError(
                "market_no_price", "MARKET order must not specify price"
            )
        return self

    model_config = {"populate_by_name": True}


class SubmitOrderResponse(BaseModel):
    """``POST /orders/submit`` 响应。"""

    client_order_id: str
    venue: str
    symbol: str
    side: Literal["BUY", "SELL"]
    order_type: Literal["MARKET", "LIMIT"]
    requested_quantity: float
    requested_price: float | None

    status: Literal["FILLED", "REJECTED"]
    filled_quantity: float = 0.0
    avg_fill_price: float | None = None
    fee: float = 0.0
    notional: float = 0.0
    rejection_reason: str | None = None

    ts_event: datetime
