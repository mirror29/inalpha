"""REST API 请求 / 响应 schema。"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic_core import PydanticCustomError


def _assume_utc_if_naive(v: datetime) -> datetime:
    return v.replace(tzinfo=UTC) if v.tzinfo is None else v


class BacktestRequest(BaseModel):
    """``POST /backtest`` 请求体。

    D-9 起：``strategy_id`` 与 ``candidate_id`` 二选一。

    - 传 ``strategy_id`` → 走内置注册表（sma_cross / mean_reversion / buy_and_hold）
    - 传 ``candidate_id`` → 走 LLM 自创策略路径（从 strategy_candidates 表读 code，
      二次过 ast_audit 后 dynamic_loader 加载）
    """

    strategy_id: str | None = Field(
        default=None,
        description="已注册的内置策略 ID（sma_cross / mean_reversion / buy_and_hold）；"
        "与 candidate_id 二选一",
        examples=["sma_cross"],
    )
    candidate_id: UUID | None = Field(
        default=None,
        description="D-9 起：LLM 自创策略候选 ID（来自 POST /strategy_candidates 响应）；"
        "与 strategy_id 二选一",
    )
    params: dict[str, Any] = Field(
        default_factory=dict,
        description="策略参数 dict；内置策略各有签名；候选策略由 LLM 在源码里写明",
        examples=[{"fast_period": 10, "slow_period": 30, "trade_size": 0.01}],
    )

    venue: str = Field(
        default="binance",
        description="数据源；按市场分类：crypto→binance / 美股→yfinance|alpaca / A 股→akshare / 全球指数→yfinance / FRED→fred",
        examples=["binance", "yfinance", "alpaca", "akshare", "fred"],
    )
    symbol: str = Field(
        ...,
        description="标的代码；支持 crypto 'BTC/USDT' / 美股 'AAPL' / 指数 '^N225' / akshare 'sh.600519' / yfinance '005930.KS' / FRED 'DFF'",
        examples=["BTC/USDT", "AAPL", "^N225", "sh.600519", "DFF"],
    )
    timeframe: str = Field(default="1h", examples=["1m", "5m", "1h", "1d"])
    from_ts: datetime = Field(..., description="起始时间（含），ISO 8601")
    to_ts: datetime = Field(..., description="结束时间（含），ISO 8601")

    initial_cash: float = Field(default=10_000.0, gt=0)
    fee_rate: float = Field(default=0.001, ge=0, lt=1)

    # D-8c 起：可选血缘 —— 把这次回测和上游 research 产物链上
    research_id: UUID | None = Field(
        default=None,
        description="触发本次回测的 research ID（来自 research.deep_dive 响应）",
    )
    strategy_hint: dict[str, Any] | None = Field(
        default=None,
        description="触发本次回测的原始 strategy_hint（审计用，可空）",
    )

    @field_validator("from_ts", "to_ts", mode="after")
    @classmethod
    def _ensure_aware(cls, v: datetime) -> datetime:
        return _assume_utc_if_naive(v)

    @model_validator(mode="after")
    def _exactly_one_strategy_source(self) -> BacktestRequest:
        """``strategy_id`` 与 ``candidate_id`` 必须二选一。"""
        has_id = bool(self.strategy_id)
        has_cand = self.candidate_id is not None
        if has_id == has_cand:  # 都给或都不给
            raise PydanticCustomError(
                "strategy_source",
                "must provide exactly one of strategy_id / candidate_id, not both / neither",
            )
        return self


class PositionSnapshot(BaseModel):
    instrument_id: str  # "BTC/USDT@binance"
    quantity: float
    avg_open_price: float
    realized_pnl: float
    generation: int


class BaselineSnapshot(BaseModel):
    """D-9 · candidate 回测的 baseline 对照（默认 buy_and_hold 同 symbol/timeframe）。

    alpha 的定义 = candidate.fitness 显著高于 baseline.fitness。内置策略路径下不带 baseline
    （内置本身就是基线，无需重复跑）。
    """

    strategy_id: str = Field(
        ...,
        description="baseline 策略 ID（默认 'buy_and_hold'）",
    )
    fitness: float | None
    sharpe: float | None
    max_drawdown_pct: float = Field(
        ...,
        description="最大回撤百分比（正数，**cap 100.0**）；超 100% 的物理穿仓由 blew_up 表达",
    )
    total_return_pct: float
    num_trades: int
    blew_up: bool = Field(
        default=False,
        description="账户是否穿仓（equity 跌破 -1%×initial_cash）；True 表示回测物理不可信",
    )


class EquityPoint(BaseModel):
    """equity_curve 单点。``ts`` 是 ISO 8601；``equity`` 是该 bar close 后的账户权益。"""

    ts: datetime
    equity: float


class BacktestResponse(BaseModel):
    """``POST /backtest`` 响应。"""

    run_id: UUID | None = Field(
        default=None,
        description="D-8c 起：落库后的 run_id；DB 不可用 / 写库失败时为 None",
    )
    research_id: UUID | None = Field(
        default=None,
        description="D-8c 起：上游 research 血缘（透传 request.research_id）",
    )
    params_hash: str | None = Field(
        default=None,
        description="D-8c 起：sha256(strategy_code|params) 前 16 位，便于去重",
    )

    strategy_id: str
    candidate_id: UUID | None = Field(
        default=None,
        description="D-9 起：若本次回测走候选路径，回填 candidate_id，便于前端串血缘",
    )
    fitness: float | None = Field(
        default=None,
        description="D-9 起：多目标 fitness（ADR-0020 §适应度函数）；裸 Sharpe 排序不可用",
    )
    baseline: BaselineSnapshot | None = Field(
        default=None,
        description="D-9 起：candidate 路径下自动并跑的 buy_and_hold 对照；"
        "内置路径下为 null（内置本身就是基线，无需重复）",
    )
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
        description="最大回撤百分比（正数，**cap 100.0**）；超 100% 的物理穿仓由 blew_up 表达",
    )
    win_rate: float | None = Field(
        default=None,
        description="round-trip 胜率（百分比）；无 round-trip 时为 null",
    )
    equity_curve: list[EquityPoint] = Field(
        default_factory=list,
        description="每根 bar 的 (ts, equity)；前端可直接画图",
    )

    blew_up: bool = Field(
        default=False,
        description="账户是否穿仓（任意时点 equity ≤ -1%×initial_cash）；True 表示本次回测"
        "物理上不可信，前端 / orchestrator 应当显式告警而非直接渲染 Sharpe / 收益率",
    )
    health_warnings: list[str] = Field(
        default_factory=list,
        description="回测物理一致性警告列表（如账户穿仓、现金透支）；非空时禁止无声渲染，"
        "agent 必须把警告原样转给用户",
    )

    final_positions: list[PositionSnapshot]


class HealthResponse(BaseModel):
    status: str = "ok"
    service: str
    version: str


# ────────────────────────────────────────────────────────────────────
# D-9 · LLM 自创策略候选（ADR-0020 E1 MVP）
# ────────────────────────────────────────────────────────────────────


class AuthorStrategyRequest(BaseModel):
    """``POST /strategy_candidates`` 请求体。

    服务端会跑三道沙盒：ast_audit（白名单） → dynamic_loader（受限 exec） →
    contract_check（必须继承 Strategy + 覆写 on_bar）。任何一道失败返 422 + 详细
    findings，让 LLM 自己改源码重试。
    """

    code: str = Field(
        ...,
        min_length=20,
        max_length=20_480,
        description="完整 Python 源码（含 1 个 Strategy 子类）。不需要 import inalpha 内部"
        "符号，已注入 globals。允许 import: math/statistics/collections/dataclasses/typing/enum/json",
    )
    description: str = Field(
        default="",
        max_length=2000,
        description="人话说明这个策略的逻辑 / 适用场景 / 关键参数",
    )
    factor_snapshot: dict[str, Any] | None = Field(
        default=None,
        description="生成时因子血缘（ADR-0047）：{venue, symbol, timeframe, as_of, "
        "factors: [{id, rank_ic, rank_ic_recent, direction, decay_state}], source}。"
        "orchestration 端 factorContext 透传；缺省 = 本候选未声明因子依赖（不伪造）",
    )


class StrategyCandidateRecord(BaseModel):
    """``GET /strategy_candidates/...`` 响应。"""

    id: UUID
    code: str
    code_hash: str
    description: str
    author: Literal["llm", "user", "system"]
    author_id: UUID | None = None
    owner_account_id: UUID | None = None
    status: Literal["candidate", "rejected", "promoted"]
    metrics: dict[str, Any] | None = None
    fitness: float | None = None
    last_backtest_run_id: UUID | None = None
    audit: dict[str, Any] | None = None
    factor_snapshot: dict[str, Any] | None = None
    created_at: datetime
    updated_at: datetime


class StrategyCandidateSummary(BaseModel):
    """``GET /strategy_candidates`` 列表响应里的一行（不带 ``code``，省带宽）。"""

    id: UUID
    code_hash: str
    description: str
    author: Literal["llm", "user", "system"]
    status: Literal["candidate", "rejected", "promoted"]
    metrics: dict[str, Any] | None = None
    fitness: float | None = None
    last_backtest_run_id: UUID | None = None
    created_at: datetime
    updated_at: datetime


class AuthorStrategyResponse(BaseModel):
    """``POST /strategy_candidates`` 响应。"""

    candidate_id: UUID
    code_hash: str
    created: bool = Field(
        ...,
        description="True = 新落库；False = 撞到现有 code_hash，返已有 ID（幂等）",
    )
    audit: dict[str, Any] = Field(
        default_factory=dict,
        description="审计摘要：通过时 {ok: true, findings: []}；失败由 422 走，"
        "本字段只在通过路径出现",
    )


class PromoteCandidateRequest(BaseModel):
    """``POST /strategy_candidates/{id}/promote`` 请求体。

    审批门要求 LLM / 用户写明 promote 理由（dataset / period / fitness vs baseline），
    落 ``audit.promotion`` 便于事后复盘"为什么把它推上线"。
    """

    reason: str = Field(
        ...,
        min_length=1,
        max_length=1000,
        description="为什么 promote：建议写明使用的回测区间、fitness vs baseline 对比、风控指标",
    )


class BacktestRunSummary(BaseModel):
    """D-8c · ``GET /backtest_runs`` 响应里的一行。"""

    run_id: UUID
    strategy_code: str
    params_hash: str | None = None
    research_id: UUID | None = None
    config: dict[str, Any] = Field(default_factory=dict)
    metrics: dict[str, Any] = Field(default_factory=dict)
    strategy_hint: dict[str, Any] | None = None
    status: str
    created_at: datetime


class BacktestTradeRecord(BaseModel):
    """D-11+ · ``GET /backtest_runs/{run_id}/trades`` 响应里的一行 —— 回测逐笔成交。

    ``realized_pnl``：本笔成交引起的持仓 realized_pnl 增量（开仓笔=0，平仓/反手笔为
    价差盈亏，不含手续费）。``intent``：open_long / open_short / close。
    """

    seq: int
    bar_ts: datetime
    bar_close: float
    side: str
    quantity: float
    order_type: str
    fill_price: float | None = None
    fee: float | None = None
    realized_pnl: float | None = None
    intent: str | None = None
    tag: str | None = None


# ────────────────────────────────────────────────────────────────────
# 单笔下单（D-8a 起步，in-memory）
# ────────────────────────────────────────────────────────────────────


class SubmitOrderRequest(BaseModel):
    """``POST /orders/submit`` 请求体。

    D-8a 范围：**单笔、同步、in-memory** —— 收到请求后立即按 ``ref_price`` 撮合，
    不维持持仓 / 不写库。给 orchestration 层的 ``executeTradePlan`` tool 用。
    """

    venue: str = Field(
        default="binance",
        description="数据源；按市场分类：crypto→binance / 美股→yfinance|alpaca / A 股→akshare / 全球指数→yfinance / FRED→fred",
        examples=["binance", "yfinance", "alpaca", "akshare", "fred"],
    )
    symbol: str = Field(
        ...,
        description="标的代码；支持 crypto 'BTC/USDT' / 美股 'AAPL' / 指数 '^N225' / akshare 'sh.600519' / yfinance '005930.KS' / FRED 'DFF'",
        examples=["BTC/USDT", "AAPL", "^N225", "sh.600519", "DFF"],
    )
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


# ────────────────────────────────────────────────────────────────────
# D-8b 查询响应 schema
# ────────────────────────────────────────────────────────────────────


class OrderRecord(BaseModel):
    """单笔订单流水（GET /orders 响应里的元素）。"""

    client_order_id: str
    venue: str | None = None
    symbol: str | None = None
    side: Literal["BUY", "SELL"]
    type: str  # MARKET / LIMIT / ... (schema CHECK)
    quantity: float
    price: float | None = None
    status: str
    filled_quantity: float = 0.0
    avg_fill_price: float | None = None
    fee: float | None = None
    notional: float | None = None
    # 这笔成交的已实现盈亏(毛口径,不减手续费):开仓/加仓单为 0,平/减仓单为实现盈亏;
    # 未成交(REJECTED 等)为 None。
    realized_pnl: float | None = None
    ts_event: datetime
    ts_init: datetime
    trade_plan_id: str | None = None


class PositionRecord(BaseModel):
    """单个持仓行（GET /positions 响应里的元素）。"""

    venue: str
    symbol: str
    quantity: float
    avg_open_price: float
    realized_pnl: float
    generation: int
    currency: str | None = Field(
        default=None,
        description="D-11：持仓计价货币（USD / CNY / USDT …）；旧行可能为 null",
    )
    updated_at: datetime


class AccountSnapshot(BaseModel):
    """GET /accounts/me 响应。

    D-11 多币种：``cash`` / ``positions_value`` / ``total_equity`` 均已折算到
    ``base_currency``；``cash_balances`` 给出折算前的按币种原始桶。FX 拿不到的币种被
    排除出折算并在 ``fx_warnings`` 点名（不静默用旧值 / 不乱猜汇率）。
    """

    account_id: str
    base_currency: str = Field(default="USD", description="D-11：报告 / 折算目标货币")
    initial_cash: float
    cash: float = Field(description="D-11：各币种桶折算到 base_currency 后的总现金")
    cash_balances: dict[str, float] = Field(
        default_factory=dict,
        description="D-11：折算前的按币种现金桶（如 {'USD': 5000, 'USDT': -1000}）",
    )
    positions_value: float = Field(
        default=0.0,
        description="所有持仓按 avg_open_price 估值并折算到 base_currency（D-8b 不接实时 mark）",
    )
    total_equity: float = Field(
        default=0.0, description="base_currency 计：cash + positions_value"
    )
    realized_pnl: float = Field(
        default=0.0,
        description="所有持仓累计实现 PnL，按各自计价货币折算到 base_currency 后汇总",
    )
    fx_warnings: list[str] = Field(
        default_factory=list,
        description="D-11：折算时 FX 不可用 / 偏旧的币种告警；非空时估值可能不完整，"
        "agent 须把告警原样转告用户",
    )
    created_at: datetime
    updated_at: datetime


# ────────────────────────────────────────────────────────────────────
# Plan API schema
# ────────────────────────────────────────────────────────────────────


class CreatePlanRequest(BaseModel):
    """``POST /plans`` 请求体。

    refPrice 不需要——paper 服务端 execute 时调 data /ticker 自取。
    """

    intent: Literal["open_long", "open_short", "close", "rebalance"]
    venue: str = Field(
        default="binance",
        description="数据源；按市场分类：crypto→binance / 美股→yfinance|alpaca / A 股→akshare / 全球指数→yfinance / FRED→fred",
        examples=["binance", "yfinance", "alpaca", "akshare", "fred"],
    )
    symbol: str = Field(
        ...,
        description="标的代码；支持 crypto 'BTC/USDT' / 美股 'AAPL' / 指数 '^N225' / akshare 'sh.600519' / yfinance '005930.KS' / FRED 'DFF'",
        examples=["BTC/USDT", "AAPL", "^N225", "sh.600519", "DFF"],
    )
    side: Literal["BUY", "SELL"]
    order_type: Literal["MARKET", "LIMIT"] = Field(default="MARKET", alias="type")
    quantity: float = Field(..., gt=0)
    price: float | None = Field(default=None, description="LIMIT 必填；MARKET 必须省略")
    rationale: str = Field(..., min_length=1)
    expire_in_seconds: int = Field(default=300, ge=1, le=3600)

    model_config = {"populate_by_name": True}


class PlanRecord(BaseModel):
    """trade_plan 完整视图。"""

    plan_id: str
    account_id: str | None = None
    intent: str
    venue: str
    symbol: str
    order_params: dict[str, Any]
    risk_params: dict[str, Any] = Field(default_factory=dict)
    rationale: str
    status: str
    approval_token: str | None = None
    approved_by: str | None = None
    rejection_reason: str | None = None
    created_at: datetime
    approved_at: datetime | None = None
    executed_at: datetime | None = None
    expire_at: datetime
    resulting_order_id: str | None = None


class ApprovePlanRequest(BaseModel):
    approver: str = Field(..., min_length=1)


class RejectPlanRequest(BaseModel):
    reason: str = Field(..., min_length=1)
    rejector: str = Field(..., min_length=1)


class ExecutePlanRequest(BaseModel):
    approval_token: str = Field(..., min_length=1, alias="approvalToken")
    model_config = {"populate_by_name": True}


class ExecutePlanResponse(BaseModel):
    plan_id: str
    plan_status: str
    order: SubmitOrderResponse


# ────────────────────────────────────────────────────────────────────
# D-11 · live runner（issue #1）
# ────────────────────────────────────────────────────────────────────


class StartStrategyRunRequest(BaseModel):
    """``POST /strategy_runs`` 请求体：给一个 promoted candidate 起 live 跑。

    candidate 表不含 venue/symbol/timeframe/params，这些在此处传（同回测请求）。
    """

    candidate_id: UUID = Field(..., description="promoted candidate 的 id")
    venue: str = Field(
        ...,
        description="数据源（**必填**，不预设市场/品种，见 CLAUDE.md §3）；按市场分类："
        "crypto→binance / 美股→yfinance|alpaca / A 股→akshare。**不要留空默认 binance**。",
        examples=["binance", "yfinance", "akshare"],
    )
    symbol: str = Field(..., examples=["BTC/USDT", "AAPL", "sh.600519"])
    timeframe: str = Field(default="1h", examples=["1m", "5m", "1h", "1d"])
    params: dict[str, Any] = Field(
        default_factory=dict,
        description="策略参数（candidate 源码 __init__ 接受的 kwargs）；缺省用策略默认值",
    )


class StrategyRunRecord(BaseModel):
    """``GET /strategy_runs`` / ``POST /strategy_runs`` 响应里的一行。"""

    id: UUID
    candidate_id: UUID
    account_id: UUID
    status: Literal["running", "stopped", "errored"]
    venue: str
    symbol: str
    timeframe: str
    params: dict[str, Any] = Field(default_factory=dict)
    last_bar_ts: datetime | None = None
    cumulative_pnl: float = 0.0
    run_log: list[dict[str, Any]] = Field(
        default_factory=list,
        description="运行日志（滚动窗口）：每条 {ts, level(info/warn/error), msg, code}",
    )
    factor_baseline: dict[str, Any] | None = Field(
        default=None,
        description="入场因子基准（ADR-0047）：起跑时拍的 /snapshot 快照，巡检对比锚点。"
        "factor 服务不可用时为 null（巡检自愈补拍）",
    )
    factor_alerts: dict[str, Any] = Field(
        default_factory=dict,
        description="衰减告警状态机（ADR-0047）：{factor_id: {state, alerted_at}}",
    )
    started_at: datetime
    stopped_at: datetime | None = None


class StrategyRunDecisionRecord(BaseModel):
    """``GET /strategy_runs/{id}/decisions`` 响应里的一行：复盘决策时间线。

    每次策略在某根 bar 产生下单意图时记一行（市场上下文 + 订单意图 + 撮合结果）。
    交叉引用：``plan_id`` → trade_plans(rationale)，``order_id`` → orders / closed_trades。
    """

    id: UUID
    run_id: UUID
    bar_ts: datetime
    bar_close: float
    side: Literal["BUY", "SELL"]
    quantity: float
    order_type: str
    limit_price: float | None = None
    tag: str | None = Field(default=None, description="策略经 Order.tag 透传的语义意图")
    intent: Literal["open_long", "open_short", "close"] | None = Field(
        default=None,
        description="按下单前持仓方向 + side 判的开/平意图，补 side（仅 BUY/SELL）缺失的多空语义",
    )
    outcome: Literal["filled", "rejected", "risk_rejected"]
    fill_price: float | None = None
    fee: float | None = None
    plan_id: UUID | None = None
    order_id: str | None = None
    reason: str | None = None
    created_at: datetime
