"""REST API + 内部数据契约。"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator


def _assume_utc_if_naive(v: datetime) -> datetime:
    return v.replace(tzinfo=UTC) if v.tzinfo is None else v


# ────────────────────────────────────────────────────────────────────
# 输入
# ────────────────────────────────────────────────────────────────────


class DeepDiveRequest(BaseModel):
    """``POST /deep_dive`` 请求体。"""

    venue: str = Field(default="binance")
    symbol: str = Field(..., examples=["BTC/USDT"])
    timeframe: str = Field(default="1h", examples=["1h", "4h", "1d"])

    as_of: datetime = Field(
        ...,
        description="研究的截止时间点；analyst 只看 as_of 之前的数据",
    )
    lookback_days: int = Field(
        default=30,
        ge=1,
        le=365,
        description="拉历史窗口长度（天）；analyst 用的 K 线全在 [as_of - N 天, as_of]",
    )
    user_question: str | None = Field(
        default=None,
        description="可选的用户原始问题，给 manager 综合时作为额外 context",
    )

    @field_validator("as_of", mode="after")
    @classmethod
    def _ensure_aware(cls, v: datetime) -> datetime:
        return _assume_utc_if_naive(v)


# ────────────────────────────────────────────────────────────────────
# 因子 / 信号 / 策略提示 —— D-8c 新增（research→strategy 机器路径）
# ────────────────────────────────────────────────────────────────────


FactorKind = Literal["momentum", "mean_reversion", "volatility", "macro", "sentiment"]
"""因子大类。后续可扩展 ``funding`` / ``onchain``，先收敛在 5 类。"""

Horizon = Literal["intraday", "swing", "position"]

StrategyFamily = Literal["trend", "mean_reversion", "buy_hold", "none"]
"""策略族。``none`` 表示因子不支持任何已注册策略族，由 compose 引擎拒绝。"""


class Factor(BaseModel):
    """单一影响因子 —— analyst 用结构化形式表达"我看到了什么"。"""

    name: str = Field(..., description="因子标识，如 'rsi_14' / 'vol_zscore_30d'")
    kind: FactorKind
    value: float | str = Field(
        ...,
        description="数值或分级。数值优先；不可量化时用 'high' / 'medium' / 'low'",
    )
    strength: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="0-1 影响强度，由 analyst 自评（compose 引擎按此加权）",
    )
    horizon: Horizon = Field(default="swing")
    explanation: str = Field(default="", description="1 句话来历，可空")


class Signal(BaseModel):
    """因子合成后的方向性信号 —— manager 把多个 factor 收敛成 1-3 条信号。"""

    direction: Literal["long", "short", "flat"]
    strength: float = Field(..., ge=0.0, le=1.0)
    timeframe: str = Field(default="1h", description="信号生效的 K 线周期")
    derived_from: list[str] = Field(
        default_factory=list,
        description="支撑此信号的 factor.name 列表（可空，但建议填）",
    )


class StrategyHint(BaseModel):
    """给 compose 引擎的机器消费提示 —— manager 输出，compose 路由到具体策略 + 参数。"""

    family: StrategyFamily = Field(default="none")
    params: dict[str, Any] = Field(
        default_factory=dict,
        description="推荐参数起点（compose 引擎可覆盖 / 收紧）",
    )
    reasoning: str = Field(
        default="",
        description="为什么选这个 family（人类可读，1-2 句）",
    )


# ────────────────────────────────────────────────────────────────────
# Analyst 输出
# ────────────────────────────────────────────────────────────────────


class AnalystBrief(BaseModel):
    """单个 analyst 的输出 —— 1 视角研究简报。"""

    analyst: Literal["technical", "fundamental", "sentiment", "risk", "macro"] = Field(
        ...,
        description="哪种分析师产出",
    )
    stance: Literal["bullish", "bearish", "neutral"] = Field(
        ...,
        description="单视角立场",
    )
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="0-1 置信度（analyst 自己声明，不强求校准）",
    )
    summary: str = Field(
        ...,
        min_length=1,
        description="1-2 句话核心结论",
    )
    key_points: list[str] = Field(
        default_factory=list,
        description="支撑结论的要点列表（≤ 5 条）",
    )
    factors: list[Factor] = Field(
        default_factory=list,
        description="D-8c 起：结构化因子列表（2-4 个）。缺省为空兼容旧 LLM 响应",
    )
    raw_excerpt: str | None = Field(
        default=None,
        description="LLM 原始响应的前 500 字（debug + 复盘用，可空）",
    )


# ────────────────────────────────────────────────────────────────────
# Manager 输出（最终 ResearchPlan）
# ────────────────────────────────────────────────────────────────────


class ResearchPlan(BaseModel):
    """``POST /deep_dive`` 响应 —— 综合 plan，给 orchestrator / trader 用。"""

    research_id: UUID = Field(
        default_factory=uuid4,
        description="D-8c 起：本次研究的唯一 ID，下游 backtest_runs / trade_plans 引用",
    )

    venue: str
    symbol: str
    timeframe: str
    as_of: datetime

    rating: Literal["overweight", "neutral", "underweight"] = Field(
        ...,
        description="最终评级：overweight=买、neutral=观望、underweight=卖/空",
    )
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="manager 综合置信度",
    )
    thesis: str = Field(
        ...,
        min_length=1,
        description="3-5 句话的核心论点（给 trader / 用户读）",
    )
    risks: list[str] = Field(
        default_factory=list,
        description="主要风险点（让 risk agent 知道要重点防什么）",
    )
    suggested_action: str = Field(
        ...,
        description="给 trader 的执行建议：开多 / 开空 / 观望 / 减仓 等（人类可读）",
    )
    factors: list[Factor] = Field(
        default_factory=list,
        description="D-8c 起：去重合并后的因子（manager 综合 analyst.factors 得到）",
    )
    signals: list[Signal] = Field(
        default_factory=list,
        description="D-8c 起：方向性信号（因子合成；compose 引擎可参考但不强依赖）",
    )
    strategy_hint: StrategyHint = Field(
        default_factory=StrategyHint,
        description="D-8c 起：策略族 + 推荐参数，给 paper.compose_strategy 消费",
    )
    briefs: list[AnalystBrief] = Field(
        default_factory=list,
        description="原始 analyst briefs（manager 综合时引用过的）",
    )
    horizon: Horizon = Field(
        default="swing",
        description="建议持仓周期：日内 / 波段（几天-2周）/ 中长线",
    )


class HealthResponse(BaseModel):
    status: str = "ok"
    service: str
    version: str
    llm_provider: str
