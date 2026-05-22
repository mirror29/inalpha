"""REST API + 内部数据契约。"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

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
# Analyst 输出
# ────────────────────────────────────────────────────────────────────


class AnalystBrief(BaseModel):
    """单个 analyst 的输出 —— 1 视角研究简报。"""

    analyst: Literal["technical", "fundamental"] = Field(
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
    raw_excerpt: str | None = Field(
        default=None,
        description="LLM 原始响应的前 500 字（debug + 复盘用，可空）",
    )


# ────────────────────────────────────────────────────────────────────
# Manager 输出（最终 ResearchPlan）
# ────────────────────────────────────────────────────────────────────


class ResearchPlan(BaseModel):
    """``POST /deep_dive`` 响应 —— 综合 plan，给 orchestrator / trader 用。"""

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
        description="给 trader 的执行建议：开多 / 开空 / 观望 / 减仓 等",
    )
    briefs: list[AnalystBrief] = Field(
        default_factory=list,
        description="原始 analyst briefs（manager 综合时引用过的）",
    )
    horizon: Literal["intraday", "swing", "position"] = Field(
        default="swing",
        description="建议持仓周期：日内 / 波段（几天-2周）/ 中长线",
    )


class HealthResponse(BaseModel):
    status: str = "ok"
    service: str
    version: str
    llm_provider: str
