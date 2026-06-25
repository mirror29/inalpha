"""请求 / 响应 schema —— 给 FastAPI 路由用，自动生成 OpenAPI。"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

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


# ────────────────────────────────────────────────────────────────────
# Panel / 横截面
# ────────────────────────────────────────────────────────────────────


class PanelScoreRequest(BaseModel):
    """``POST /panel/score`` —— 一篮子标的的横截面因子评估（选标的 + 横截面有效性）。

    与单标的 ``/score`` 正交：那边判"一个标的的因子时序有没有择时力"，这里判"在
    universe 里按因子排序能不能选出好标的"（横截面 rank-IC + 最新排名）。
    """

    venue: str = Field(default="binance")
    symbols: list[str] = Field(
        ...,
        min_length=2,
        max_length=50,
        description="universe 标的集（同 venue/timeframe）。**非 PIT**——调用方给定的固定"
        "集，历史成分快照未建，响应 is_pit=false 显式标注存活者偏差风险",
        examples=[["AAPL", "MSFT", "GOOGL", "AMZN", "META"]],
    )
    timeframe: str = Field(default="1d")
    as_of: datetime | None = Field(
        default=None, description="评估截止时刻（只用 <= as_of 的 bar）；None = 现在"
    )
    lookback_bars: int = Field(default=720, ge=120, le=10000)
    horizon_bars: int = Field(default=5, ge=1, le=60)
    min_symbols: int = Field(
        default=3,
        ge=2,
        le=50,
        description="某期参与横截面排名的最少有效标的数；不足则该期不排名（残缺池排名是伪信号）",
    )
    factor_ids: list[str] | None = Field(
        default=None, description="None = 全部价量/自定义因子（macro 不参与横截面，无区分度）"
    )

    @field_validator("as_of", mode="after")
    @classmethod
    def _aware(cls, v: datetime | None) -> datetime | None:
        return _assume_utc_if_naive(v) if v is not None else None


class PanelRankEntry(BaseModel):
    symbol: str
    value: float = Field(description="该标的最近有效横截面时点的因子值")
    rank_pct: float = Field(description="该值在 universe 内的分位排名 (0,1]，升序")


class PanelFactorResult(BaseModel):
    """单因子在 universe 上的横截面有效性 + 最新排名。"""

    factor_id: str
    source: str
    name: str
    kind: str
    ic_kind: str = Field(default="cross_sectional", description="与单标的 timeseries IC 区分")
    cross_sectional_ic: float = Field(
        description="逐期横截面 rank-IC 的均值：每期对全池按因子排序 vs 跨标的前瞻收益"
    )
    icir: float = Field(description="横截面 IC 序列的 mean/std（稳定性）")
    n_periods: int = Field(description="参与的横截面期数（有效标的≥min_symbols 的 t）")
    mean_valid_symbols: float = Field(description="每期平均有效标的数")
    low_confidence: bool = Field(description="有效期数不足，横截面 IC 不可靠")
    latest_ranking: list[PanelRankEntry] = Field(
        default_factory=list,
        description="最近有效横截面的排名（按因子值升序）——选标的直接用：取最低=列表首，最高=列表尾",
    )


class PanelScoreResponse(BaseModel):
    venue: str
    timeframe: str
    as_of: datetime
    horizon_bars: int
    symbols: list[str]
    bars_used: dict[str, int] = Field(description="每标的取到的 bar 数（**不代表新鲜**，见 latest_bar_ts）")
    latest_bar_ts: dict[str, str | None] = Field(
        default_factory=dict,
        description="每标的最后一根 bar 的 ISO ts（None=无数据）。**判 freshness 看它距 as_of "
        "的间隔,不要看 bar 数量**（§3.1）——panel 走 fresh=False 读缓存,某标的可能是几天前的",
    )
    is_pit: bool = Field(
        default=False,
        description="universe 是否 point-in-time。**当前恒 false**（历史成分快照未建）"
        "——用固定标的集，带存活者偏差，横截面证据强度打折，勿当 PIT 结论引用",
    )
    universe_note: str = Field(description="universe 口径说明（含降级原因）")
    factors: list[PanelFactorResult] = Field(default_factory=list)
    ic_null_benchmark: float = Field(
        default=0.0, description="选择效应基准（读法/局限见 ScoreResponse 同名字段）"
    )
    reason: str | None = Field(default=None, description="factors 为空时的原因")


# ── 自定义因子表达式（D-12 · 因子发现 L1）────────────────────────────


class CustomScoreRequest(BaseModel):
    """``POST /custom/score`` —— 一个受限 qlib 风格表达式的一站式评估。"""

    expression: str = Field(
        ...,
        min_length=2,
        max_length=2000,
        description="受限 DSL 表达式，如 ($close - Ref($close, 5)) / Ref($close, 5)。"
        "列引用 $close/$open/$high/$low/$volume；算子白名单见 expression.py（Ref/Delta "
        "的 lag 必须正整数，统计算子必须带 1..500 的 window 字面量——防 lookahead）",
    )
    name: str | None = Field(
        default=None, max_length=120, description="人话名（缺省用表达式截断）"
    )
    venue: str = Field(default="binance")
    symbol: str = Field(..., examples=["BTC/USDT"])
    timeframe: str = Field(default="1h")
    as_of: datetime | None = Field(
        default=None, description="评估截止时刻（只用 <= as_of 的 bar）；None = 现在"
    )
    lookback_bars: int = Field(default=720, ge=120, le=10000)
    horizon_bars: int = Field(default=5, ge=1, le=60)
    quantiles: int = Field(default=5, ge=2, le=10)

    @field_validator("as_of", mode="after")
    @classmethod
    def _aware(cls, v: datetime | None) -> datetime | None:
        return _assume_utc_if_naive(v) if v is not None else None


class CorrelatedFactor(BaseModel):
    factor_id: str
    corr: float = Field(description="与库内因子的 |spearman|（同 df 现算时序）")


class CustomScoreResponse(BaseModel):
    """``POST /custom/score`` 响应：effectiveness + p 值 + 与库相关性一次出全。"""

    venue: str
    symbol: str
    timeframe: str
    as_of: datetime
    horizon_bars: int
    bars_used: int
    available: bool = Field(description="计算是否成功（false 看 reason）")
    reason: str | None = None
    expression: str
    factor: FactorEffectiveness | None = Field(
        default=None, description="factor_id=custom.<sha16>；available=false 时为 null"
    )
    ic_pvalue: float | None = Field(
        default=None,
        description="rank_ic 的双侧 p 值（t 近似 + n_eff 按 1/horizon 折算，参考量级"
        "非严格检验）。多次尝试表达式时自行累计次数，propose 前会做批内 BH 校正",
    )
    top_correlated: list[CorrelatedFactor] = Field(
        default_factory=list, description="与库内价量因子的 |spearman| top5——查重复造轮子"
    )
    max_corr: float | None = None
    is_likely_redundant: bool = Field(
        default=False,
        description="max_corr ≥ 去相关阈值（默认 0.85）——大概率是已有因子换皮，别 propose",
    )


# ── 因子候选池（D-12 · 因子发现 L1 · ADR-0019 简化执行）──────────────


class ProposeFactorRequest(BaseModel):
    """``POST /candidates`` —— 把通过评估的表达式提为候选（status=pending_review）。"""

    expression: str = Field(..., min_length=2, max_length=2000)
    hypothesis: str = Field(
        ...,
        min_length=20,
        max_length=2000,
        description="经济学故事门：**为什么**这个因子该有效（行为偏差/结构性约束/信息扩散…），"
        "不收只有数字没有故事的候选",
    )
    name: str | None = Field(default=None, max_length=120)
    proposed_by: str = Field(default="agent", max_length=120)
    venue: str | None = Field(default=None, description="评估上下文（复核用）")
    symbol: str | None = None
    timeframe: str | None = None
    test_results: dict[str, Any] = Field(
        default_factory=dict,
        description="评估快照：rank_ic / icir / decay_state / max_corr / ic_pvalue / "
        "adjusted_p 等（/custom/score 的产物 + workflow 的 BH 校正结果）",
    )
    batch_id: UUID | None = Field(
        default=None, description="L1 批次 id（多重检验审计锚点）"
    )
    n_tested: int = Field(
        default=1,
        ge=1,
        le=10_000,
        description="本批/本会话累计评估过多少个候选表达式（BH 校正的 m，**如实自报**）",
    )


class ProposeFactorResponse(BaseModel):
    candidate_id: UUID
    expression_hash: str
    created: bool = Field(description="false = 撞同表达式已有候选，返老行（幂等）")
    status: str = Field(default="pending_review")


class FactorCandidateRecord(BaseModel):
    """``GET /candidates`` 一行。"""

    id: UUID
    expression: str
    expression_hash: str
    name: str | None = None
    hypothesis: str
    proposed_by: str
    venue: str | None = None
    symbol: str | None = None
    timeframe: str | None = None
    test_results: dict[str, Any] = Field(default_factory=dict)
    batch_id: UUID | None = None
    n_tested: int = 1
    status: Literal["pending_review", "rejected", "registered"]
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None
    review_note: str | None = None
    created_at: datetime
    updated_at: datetime


class ReviewFactorCandidateRequest(BaseModel):
    """``POST /candidates/{id}/review`` —— 人工审核（**不挂任何 LLM tool**）。"""

    action: Literal["register", "reject"]
    reviewed_by: str = Field(..., min_length=1, max_length=120)
    note: str | None = Field(default=None, max_length=1000)
