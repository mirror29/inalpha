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
    source: str = Field(description="pandas_ta | alpha101 | qlib_alpha158 | macro")
    name: str
    kind: str = Field(
        description="momentum | mean_reversion | volatility | volume | trend | macro"
    )
    needs_universe: bool = Field(
        default=False, description="true = 横截面因子，需多标的 universe，本期单标的不计算"
    )
    direction_hint: int = Field(
        default=0, description="先验方向 +1/-1/0；真实方向以 score 的 rank_ic 符号为准"
    )
    available: bool = Field(default=True, description="该源是否已装/启用")
    extras: dict[str, str] = Field(
        default_factory=dict,
        description="附加约束，如 macro 因子的 timeframes（仅 1d/1wk 计算）与 FRED series",
    )


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
    rank_ic_recent: float = Field(
        default=0.0,
        description="近 1/3 样本窗的 Rank IC：与 rank_ic 同号且量级接近≈稳定，反号/趋零≈正在衰减",
    )
    icir: float = Field(description="分段 IC 的均值 / 标准差")
    turnover: float = Field(
        default=0.0,
        description="因子换手：1 - spearman(f_t, f_{t-1})，0≈信号几乎不动；高 IC+高换手应打折",
    )
    sample_size: int = Field(description="有效（因子, 前瞻收益）对数")
    corr_pruned: list[str] = Field(
        default_factory=list,
        description="snapshot 去相关时被本因子挤掉的同质因子 id（|spearman| ≥ 阈值）",
    )
    quantile_returns: list[QuantileStat] = Field(default_factory=list)
    long_short_return: float = Field(default=0.0, description="top 分位 - bottom 分位 平均前瞻收益")
    direction: int = Field(description="择时方向 +1/-1/0（sign(rank_ic)，过阈值才非 0）")
    strength: float = Field(description="|rank_ic| 归一到 0-1")
    low_confidence: bool = Field(description="样本不足，不应据此择时")
    decay_state: str = Field(
        default="decaying",
        description="衰减三态（ADR-0047 D2 单一权威）：decaying=recent 反号/趋零；"
        "stable=量级保住 60%+；fading=其间。前端徽章与 live runner 巡检都以本字段为准",
    )


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
    ic_null_benchmark: float = Field(
        default=0.0,
        description="选择效应基准（ADR-0043 D4 延伸）：本批 N 个候选、当前样本量下"
        "纯噪声能跑出的期望最大 |IC|（Bailey–López de Prado E[max] 近似）。"
        "top 因子 |rank_ic| 不显著高于该值 ⇒ 可能是从 N 个里挑出来的选择效应。"
        "局限：n_eff 按 1/horizon 折算是启发式、假设候选独立——是地板不是假设检验，"
        "只供判断，不做自动剔除",
    )


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
    available: bool = Field(
        description=(
            "factor 计算是否成功（false 时 caller 应降级）。"
            "**≠ 有可用信号**：全部因子低置信时仍为 true 但 top_factors=[]，"
            "判有无信号看 top_factors 非空，top 为空时 reason 必有原因"
        )
    )
    reason: str | None = None
    top_factors: list[FactorEffectiveness] = Field(default_factory=list)
    candidates_evaluated: int = Field(
        default=0,
        description="本次共评估的候选因子数——top-N 是从这么多里挑的（多重检验背景，ADR-0043 D4）",
    )
    low_confidence_count: int = Field(
        default=0, description="样本不足被排除排序的因子数（区分'没因子'和'有因子但样本不够'）"
    )
    ic_null_benchmark: float = Field(
        default=0.0,
        description="选择效应基准：candidates_evaluated 个候选、当前样本量下纯噪声的"
        "期望最大 |IC|（读法/局限见 ScoreResponse 同名字段）。top1 的 |rank_ic| 不显著"
        "高于此值时引用要谨慎",
    )


class ComputeErrorResponse(BaseModel):
    available: bool = False
    reason: str
    raw: dict[str, Any] | None = None
