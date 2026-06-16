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
    personas: list[str] | None = Field(
        default=None,
        description="可选：额外启用的投资大师人格 analyst（buffett / lynch / wood / "
        "burry / druckenmiller / marks，详见 analysts/personas）。None 或空 = 不跑 "
        "persona，核心 analyst 行为不变；指定时每个 persona 多一次 LLM 调用。",
    )
    language: str | None = Field(
        default=None,
        description="可选：期望输出语言（自然语言名，如 'English' / '中文'）。传入时所有 "
        "analyst / 辩论 / manager 的自然语言输出都用该语言（Fix C）；None = 保持模型默认。",
    )

    @field_validator("as_of", mode="after")
    @classmethod
    def _ensure_aware(cls, v: datetime) -> datetime:
        return _assume_utc_if_naive(v)

    @field_validator("personas", mode="after")
    @classmethod
    def _validate_personas(cls, v: list[str] | None) -> list[str] | None:
        """拒绝未知 persona key —— 直接调用方（集成测试 / 未来新调用方）的防线。

        orchestrator 侧 TS ``z.enum`` 已挡非法 key，但 Python 服务被直接调用时
        ``list[str]`` 无校验：无效 key 会在 runner 里静默丢弃、返 HTTP 200 且 briefs
        里没有对应 persona、无任何报错。这里在 API 边界 fail-fast，给清楚的错误。

        合法 key 从 ``PERSONA_ANALYSTS`` 注册表动态派生（懒 import 避免与 analysts
        包的循环依赖），新增 persona 无需改本处。
        """
        if not v:
            return v
        from .analysts.personas import PERSONA_ANALYSTS

        unknown = [k for k in v if k not in PERSONA_ANALYSTS]
        if unknown:
            valid = ", ".join(sorted(PERSONA_ANALYSTS))
            raise ValueError(f"unknown persona key(s): {unknown}; valid keys: {valid}")
        return v


# ────────────────────────────────────────────────────────────────────
# 因子 / 信号 / 策略提示 —— D-8c 新增（research→strategy 机器路径）
# ────────────────────────────────────────────────────────────────────


FactorKind = Literal["momentum", "mean_reversion", "volatility", "macro", "sentiment"]
"""因子大类。后续可扩展 ``funding`` / ``onchain``，先收敛在 5 类。"""

Horizon = Literal["intraday", "swing", "position"]

StrategyFamily = Literal[
    "trend", "mean_reversion", "buy_hold", "breakout", "volatility", "none"
]
"""策略族。docs/miro/11 M4 起加 breakout（Donchian 通道突破）/ volatility（ATR 通道）。
``none`` 表示因子不支持任何已注册策略族，由 compose 引擎拒绝。"""


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


# ────────────────────────────────────────────────────────────────────
# 辩论日志 —— D-9 引入，记录 Bull/Bear 多轮对喷
# ────────────────────────────────────────────────────────────────────


class DebateTurn(BaseModel):
    """Bull / Bear / Risk 一轮发言。

    辩论协调器轮换调用各 researcher；每次发言追加一条 ``DebateTurn`` 到
    ``ResearchPlan.debate_log``，按时间顺序组成完整辩论史。
    """

    role: Literal["bull", "bear", "risk"] = Field(
        ...,
        description="发言角色：bull 看多 / bear 看空 / risk 风险官（research-hub #6 三方制）",
    )
    round: int = Field(
        ..., ge=1, description="第几轮（1-based）；同轮内 bull → bear → risk 固定顺序"
    )
    content: str = Field(..., min_length=1, description="LLM 该轮发言原文")


class AnalystBrief(BaseModel):
    """单个 analyst 的输出 —— 1 视角研究简报。"""

    analyst: Literal[
        "technical", "fundamental", "sentiment", "risk", "macro", "valuation",
        # ADR-0037 §A：投资大师人格 persona（可选启用）。runner 的合法类型集从本
        # Literal 动态派生（typing.get_args），新增 persona 只需在这里加值。
        "persona_buffett", "persona_lynch", "persona_wood",
        "persona_burry", "persona_druckenmiller", "persona_marks",
    ] = Field(
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
    debate_log: list[DebateTurn] = Field(
        default_factory=list,
        description="D-9 起：Bull/Bear(/Risk) 辩论日志，manager 综合时已读过；"
        "下游 trader / UI 可重现辩论过程",
    )
    horizon: Horizon = Field(
        default="swing",
        description="建议持仓周期：日内 / 波段（几天-2周）/ 中长线",
    )

    # ── research-hub #6：决策链路可观测（为什么辩了 / 为什么停 / 怎么权衡的）──
    debate_trigger: str | None = Field(
        default=None,
        description="research-hub #6：debate 触发判定结果。前缀固定三选一——"
        "'contested: '（分歧触发）/ 'skipped: '（同向跳过）/ 'always: '（强制直跑），"
        "下游可安全 startswith 解析；None = max_debate_rounds=0 未启用辩论",
    )
    debate_stop_reason: str | None = Field(
        default=None,
        description="research-hub #6：辩论终止原因（completed / converged 软早停 / timeout）；"
        "None = 本次没跑辩论",
    )
    synthesis_reasoning: str | None = Field(
        default=None,
        description="research-hub #6：manager 综合的权衡自述（怎么调和分歧、辩论谁占上风），"
        "复盘「为什么是这个 rating」的第一证据；LLM 没给则为 None",
    )


class HealthResponse(BaseModel):
    status: str = "ok"
    service: str
    version: str
    llm_provider: str
