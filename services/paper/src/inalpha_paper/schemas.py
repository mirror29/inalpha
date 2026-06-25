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

    validation_split: float = Field(
        default=0.7,
        ge=0.0,
        lt=1.0,
        description="D-12 · train/holdout 时间切分比例（按 bar 数）；默认 0.7 = 前 70% "
        "train + 后 30% holdout，响应带 validation 块。传 0 关闭（如刻意全窗回测）",
    )

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


class CVBacktestRequest(BacktestRequest):
    """``POST /backtest/cv`` 请求体（ADR-0028）—— 在 BacktestRequest 上加 splitter 配置。

    继承 strategy_id/candidate_id 二选一校验 + 数据窗口字段；``validation_split`` 在 CV
    路径无意义（被忽略）。
    """

    splitter: Literal["cpcv", "walk_forward", "purged_kfold"] = Field(
        default="cpcv",
        description="时序 CV 切分器：cpcv（组合式 purged，多路径，最强）/ walk_forward / "
        "purged_kfold。bar < 200 时 cpcv 自动回落 walk_forward。",
    )
    n_folds: int = Field(default=6, ge=2, le=20, description="cpcv/kfold 分组数")
    n_test_folds: int = Field(
        default=2, ge=1, description="cpcv 每组合取作 test 的组数（须 < n_folds）"
    )
    embargo_pct: float = Field(
        default=0.05, ge=0.0, lt=1.0, description="purge+embargo 占总 bar 比例（按 bar 数）"
    )
    wf_test_size: int = Field(default=21, ge=1, description="walk_forward 每折 test bar 数")
    wf_train_size: int = Field(
        default=252, ge=1, description="walk_forward train 窗口 bar 数"
    )

    @model_validator(mode="after")
    def _check_cpcv_test_folds(self) -> CVBacktestRequest:
        """cpcv 要求 n_test_folds < n_folds（> 1 才是组合式）。"""
        if self.splitter == "cpcv" and not 1 <= self.n_test_folds < self.n_folds:
            raise PydanticCustomError(
                "cpcv_test_folds",
                "n_test_folds must be in [1, n_folds) for cpcv",
            )
        return self


class CVBacktestResponse(BaseModel):
    """``POST /backtest/cv`` 响应（ADR-0028）—— 多路径 OOS Sharpe 分布 + DSR。"""

    symbol: str
    timeframe: str
    n_bars: int
    splitter_used: str = Field(description="实际用的 splitter（cpcv 不足回落时 != 请求值）")
    n_paths: int
    n_splits: int
    sharpe_per_path: list[float]
    max_dd_per_path: list[float]
    sharpe_p5: float
    sharpe_p50: float
    sharpe_p95: float
    sharpe_mean: float
    dsr: float | None = None
    dsr_p_value: float | None = None
    note: str | None = Field(
        default=None, description="回落 / 降级说明（如 cpcv → walk_forward）"
    )


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


class SharpeCI(BaseModel):
    """ADR-0027 · 单次回测的 Bootstrap Sharpe 95% 置信区间（年化口径）。

    ``includes_zero=True`` 是核心信号：表示 Sharpe 统计上不显著为正——回测曲线
    "看起来好"但禁不起重采样检验，agent 不应把这个 Sharpe 当卖点（详 ADR-0026
    反过拟合体系）。
    """

    lower: float = Field(..., description="95% CI 下界（年化 Sharpe）")
    upper: float = Field(..., description="95% CI 上界（年化 Sharpe）")
    includes_zero: bool = Field(
        ...,
        description="CI 是否横跨 0；True ⇒ Sharpe 统计上不显著为正，回测好看但不可信",
    )


class ValidationSegment(BaseModel):
    """holdout 验证的单个时间段（train 或 holdout）指标。"""

    sharpe: float | None = Field(
        default=None, description="该段年化 Sharpe；样本不足 / 零波动时 null"
    )
    total_return_pct: float
    max_drawdown_pct: float
    num_trades: int = Field(..., description="该段成交笔数（fills 口径）")
    num_bars: int


class ValidationBlock(BaseModel):
    """D-12 · holdout 时间切分验证（单次引擎运行，按 equity_curve 切段，零额外 CPU）。

    **语义边界**：这是"窗口内一致性检验"，不是盲样本外——agent 看得到 holdout
    指标，反复对着它调参就会间接过拟合 holdout。纪律：调参看 train 段，holdout
    只作裁判（orchestrator prompt 同步约束）。
    """

    split_ratio: float = Field(..., description="train 段占比（按 bar 数切）")
    train: ValidationSegment
    holdout: ValidationSegment
    decay_ratio: float | None = Field(
        default=None,
        description="holdout_sharpe / train_sharpe；< 0.5 或 holdout_sharpe < 0 = "
        "过拟合信号。train_sharpe ≤ 0 或任一段 Sharpe 无定义时为 null（看 flags）",
    )
    holdout_sharpe_ci_includes_zero: bool | None = Field(
        default=None,
        description="holdout 段 bootstrap Sharpe 95% CI 是否横跨 0；true = 统计上"
        "不显著为正。样本不足时 null",
    )
    flags: list[str] = Field(
        default_factory=list,
        description="insufficient_sample / train_sharpe_nonpositive / sharpe_undefined",
    )


class SensitivityRequest(BaseModel):
    """``POST /backtest/sensitivity`` 请求体（D-12 · 参数邻域敏感性检查）。

    ``params`` 必须传**最终收敛的完整参数 dict**——源码里的默认值不在扰动范围。
    """

    strategy_id: str | None = Field(default=None, description="内置策略 ID；与 candidate_id 二选一")
    candidate_id: UUID | None = Field(default=None, description="候选 UUID；与 strategy_id 二选一")
    params: dict[str, Any] = Field(
        ...,
        description="最终参数 dict（每个数值参数做 one-at-a-time ±pct 扰动）",
    )

    venue: str = Field(default="binance")
    symbol: str = Field(...)
    timeframe: str = Field(default="1h")
    from_ts: datetime = Field(...)
    to_ts: datetime = Field(...)
    initial_cash: float = Field(default=10_000.0, gt=0)
    fee_rate: float = Field(default=0.001, ge=0, lt=1)

    pct: float = Field(default=0.2, gt=0, lt=1, description="扰动幅度（±pct）")
    max_combos: int = Field(default=16, ge=2, le=16, description="邻域组合数上限")

    @field_validator("from_ts", "to_ts", mode="after")
    @classmethod
    def _ensure_aware(cls, v: datetime) -> datetime:
        return _assume_utc_if_naive(v)

    @model_validator(mode="after")
    def _exactly_one_strategy_source(self) -> SensitivityRequest:
        # is not None 而非 bool()：strategy_id="" + candidate_id=<uuid> 时 bool("")=False
        # 会误判互斥通过，随后端点层报误导性的 "unknown strategy_id ''"（CR #86）。
        has_id = self.strategy_id is not None
        has_cand = self.candidate_id is not None
        if has_id == has_cand:
            raise PydanticCustomError(
                "strategy_source",
                "must provide exactly one of strategy_id / candidate_id, not both / neither",
            )
        return self


class SensitivityNeighbor(BaseModel):
    """单个邻域组合的结果。``fitness`` 为 null 表示该组合非法 / 运行失败。"""

    params: dict[str, Any]
    fitness: float | None = None
    error: str | None = None


class SensitivityStats(BaseModel):
    """邻域 fitness 分布摘要。"""

    mean: float | None
    std: float | None
    worst: float | None
    n_ok: int
    n_failed: int


class SensitivityResponse(BaseModel):
    """``POST /backtest/sensitivity`` 响应。

    verdict 判读：
    - ``robust``：邻域 fitness 没有断崖——参数面是高原
    - ``cliff``：邻域最差 < 0.5 × base——单参数小扰动掉一半，过拟合信号，**不应 promote**
    - ``insufficient``：成功邻域 < 4 组或 base fitness ≤ 0，结论不可靠
    """

    candidate_id: UUID | None
    strategy_id: str | None
    base_fitness: float
    pct: float
    neighbors: list[SensitivityNeighbor]
    stats: SensitivityStats
    verdict: Literal["robust", "cliff", "insufficient"]


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
    validation: ValidationBlock | None = Field(
        default=None,
        description="D-12 起：holdout 时间切分验证（validation_split > 0 时带）；"
        "decay_ratio < 0.5 或 holdout.sharpe < 0 = 过拟合信号。曲线太短切不动时 null",
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
    protective_exits: int = Field(
        default=0,
        description="ADR-0052：本次回测框架级持仓保护止损触发的平仓笔数（tag ∈ "
        "stop_loss/take_profit/trailing_stop_loss）。>0 说明灾难兜底生效过几次——回测如实"
        "反映未来 live 也会有的兜底,agent 可据此向用户说明风险被框架封住了几次。",
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
    sharpe_ci: SharpeCI | None = Field(
        default=None,
        description="ADR-0027 防过拟合：Bootstrap Sharpe 95% 置信区间（年化，与 sharpe 同口径）。"
        "includes_zero=True 表示 Sharpe 统计上不显著为正——回测'看起来好'但不可信，"
        "agent 不应把 Sharpe 当卖点。样本不足 / 穿仓 / 无波动时为 null",
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
    warnings: list[str] = Field(
        default_factory=list,
        description="D-12 · 非阻断告警：如 factor_snapshot 里有 decay_state 已是 "
        "fading/decaying 的因子（策略设计时就该知道依据在衰减，而不是等 promote "
        "后巡检才发现）。落库照常，agent 必须把告警转告用户",
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

    trading_mode: Literal["spot", "perp"] = Field(
        default="spot",
        description="spot(默认,现货 long-only)或 perp(USDT-M 永续 + 逐仓,放开做空/杠杆;"
        "仅 crypto 永续标的如 BTC/USDT:USDT 生效)",
    )
    leverage: int = Field(
        default=1, ge=1, le=20,
        description="杠杆倍数(perp 用,1..20);spot 恒 1",
    )

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
