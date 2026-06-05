"""请求 / 响应 schema —— 给 FastAPI 路由用，自动生成 OpenAPI。"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator


def _assume_utc_if_naive(v: datetime) -> datetime:
    return v.replace(tzinfo=UTC) if v.tzinfo is None else v


class HealthResponse(BaseModel):
    status: str = "ok"
    service: str = "factor"
    version: str
    qlib_enabled: bool = False
    adapters: dict[str, bool] = Field(
        default_factory=dict, description="各因子源是否可用（source -> available）"
    )


# ────────────────────────────────────────────────────────────────────
# Catalog
# ────────────────────────────────────────────────────────────────────


class FactorSpecOut(BaseModel):
    """目录里的单个因子定义。"""

    factor_id: str
    source: str = Field(description="pandas_ta | alpha101 | qlib_alpha158")
    name: str
    kind: str = Field(description="momentum | mean_reversion | volatility | volume | trend")
    needs_universe: bool = Field(
        default=False, description="true = 横截面因子，需多标的 universe，本期单标的不计算"
    )
    direction_hint: int = Field(
        default=0, description="先验方向 +1/-1/0；真实方向以 score 的 rank_ic 符号为准"
    )
    available: bool = Field(default=True, description="该源是否已装/启用")


class CatalogResponse(BaseModel):
    factors: list[FactorSpecOut]
    sources: dict[str, bool] = Field(description="source -> available")


# ────────────────────────────────────────────────────────────────────
# Compute
# ────────────────────────────────────────────────────────────────────


class ComputeRequest(BaseModel):
    venue: str = Field(default="binance")
    symbol: str = Field(..., examples=["BTC/USDT"])
    timeframe: str = Field(default="1h")
    from_ts: datetime
    to_ts: datetime
    factor_ids: list[str] | None = Field(
        default=None, description="指定要算的因子 id；None = 全部可时序计算的因子"
    )

    @field_validator("from_ts", "to_ts", mode="after")
    @classmethod
    def _aware(cls, v: datetime) -> datetime:
        return _assume_utc_if_naive(v)


class FactorSeriesPoint(BaseModel):
    ts: datetime
    value: float | None


class ComputeResponse(BaseModel):
    venue: str
    symbol: str
    timeframe: str
    bars_used: int
    series: dict[str, list[FactorSeriesPoint]] = Field(
        description="factor_id -> 时序点（末尾 NaN 已保留为 null，前置 warmup 段亦为 null）"
    )


# ────────────────────────────────────────────────────────────────────
# Score / Snapshot（有效性）
# ────────────────────────────────────────────────────────────────────


class QuantileStat(BaseModel):
    q: int
    mean_return: float
    sample_size: int


class FactorEffectiveness(BaseModel):
    """单因子在给定标的 / 周期 / 前瞻 horizon 下的有效性。"""

    factor_id: str
    source: str
    name: str
    kind: str
    value: float | None = Field(description="as_of 时点的最新因子值")
    rank_ic: float = Field(description="时序 Rank IC：spearman(rank(factor), rank(fwd_return))")
    icir: float = Field(description="分段 IC 的均值 / 标准差")
    sample_size: int = Field(description="有效（因子, 前瞻收益）对数")
    quantile_returns: list[QuantileStat] = Field(default_factory=list)
    long_short_return: float = Field(default=0.0, description="top 分位 - bottom 分位 平均前瞻收益")
    direction: int = Field(description="择时方向 +1/-1/0（sign(rank_ic)，过阈值才非 0）")
    strength: float = Field(description="|rank_ic| 归一到 0-1")
    low_confidence: bool = Field(description="样本不足，不应据此择时")


class ScoreRequest(BaseModel):
    venue: str = Field(default="binance")
    symbol: str = Field(..., examples=["BTC/USDT"])
    timeframe: str = Field(default="1h")
    as_of: datetime | None = Field(
        default=None, description="评估截止时刻（只用 <= as_of 的 bar，绝不用未来数据）；None = 现在"
    )
    lookback_bars: int = Field(
        default=720, ge=120, le=10000, description="向前取多少根 bar 算有效性"
    )
    horizon_bars: int = Field(
        default=5, ge=1, le=60, description="前瞻收益窗口（未来 N 根 bar 的累计收益）"
    )
    quantiles: int = Field(default=5, ge=2, le=10)
    factor_ids: list[str] | None = Field(default=None, description="None = 全部可时序计算因子")

    @field_validator("as_of", mode="after")
    @classmethod
    def _aware(cls, v: datetime | None) -> datetime | None:
        return _assume_utc_if_naive(v) if v is not None else None


class ScoreResponse(BaseModel):
    venue: str
    symbol: str
    timeframe: str
    as_of: datetime
    horizon_bars: int
    bars_used: int
    factors: list[FactorEffectiveness]


class SnapshotRequest(BaseModel):
    venue: str = Field(default="binance")
    symbol: str = Field(..., examples=["BTC/USDT"])
    timeframe: str = Field(default="1h")
    as_of: datetime | None = None
    lookback_bars: int = Field(default=720, ge=120, le=10000)
    horizon_bars: int = Field(default=5, ge=1, le=60)
    top_n: int | None = Field(default=None, description="None = 用服务端默认 snapshot_top_n")

    @field_validator("as_of", mode="after")
    @classmethod
    def _aware(cls, v: datetime | None) -> datetime | None:
        return _assume_utc_if_naive(v) if v is not None else None


class SnapshotResponse(BaseModel):
    """喂给 research analyst / agent timing 的紧凑形状：只回 top-N 有效因子。"""

    venue: str
    symbol: str
    timeframe: str
    as_of: datetime
    horizon_bars: int
    bars_used: int
    available: bool = Field(description="factor 计算是否成功（false 时 caller 应降级）")
    reason: str | None = None
    top_factors: list[FactorEffectiveness] = Field(default_factory=list)


class ComputeErrorResponse(BaseModel):
    available: bool = False
    reason: str
    raw: dict[str, Any] | None = None
