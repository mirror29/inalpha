/**
 * services/paper 客户端。
 */
import { HttpClient } from "./http.js";

export type PositionSnapshot = {
  instrument_id: string;
  quantity: number;
  avg_open_price: number;
  realized_pnl: number;
  generation: number;
};

/** D-9 · candidate 回测的 buy_and_hold 对照（runner candidate 分支自动并跑）。 */
export type BaselineSnapshot = {
  strategy_id: string;
  fitness: number | null;
  sharpe: number | null;
  /** 最大回撤百分比（正数，cap 100.0）；超 100% 的物理穿仓由 blew_up 表达 */
  max_drawdown_pct: number;
  total_return_pct: number;
  num_trades: number;
  /** D-9 起：账户是否穿仓；true 表示本次 baseline 回测物理不可信 */
  blew_up?: boolean;
};

/** D-12 · holdout 验证的单段（train / holdout）指标 */
export type ValidationSegment = {
  sharpe: number | null;
  total_return_pct: number;
  max_drawdown_pct: number;
  num_trades: number;
  num_bars: number;
};

/**
 * D-12 · holdout 时间切分验证（单次引擎运行按 equity_curve 切段）。
 * decay_ratio < 0.5 或 holdout.sharpe < 0 = 过拟合信号。
 * 注意这是"窗口内一致性检验"非盲 OOS：调参看 train，holdout 只作裁判。
 */
export type ValidationBlock = {
  split_ratio: number;
  train: ValidationSegment;
  holdout: ValidationSegment;
  /** holdout_sharpe / train_sharpe；train ≤ 0 或 Sharpe 无定义时 null（看 flags） */
  decay_ratio: number | null;
  /** holdout bootstrap Sharpe 95% CI 是否横跨 0；true = 统计上不显著为正 */
  holdout_sharpe_ci_includes_zero: boolean | null;
  flags: string[];
};

export type BacktestReport = {
  /** D-8c 起：落库后 run_id，可作血缘锚点供 trade.create_plan 引用 */
  run_id: string | null;
  /** D-8c 起：上游 research 血缘（透传） */
  research_id: string | null;
  /** D-8c 起：sha256(strategy_code|params) 前 16 hex，去重用 */
  params_hash: string | null;

  /** 内置策略 ID 或 'candidate:<uuid>'（D-9 candidate 路径） */
  strategy_id: string;
  /** D-9 起：candidate 路径下回填 candidate_id；内置路径为 null */
  candidate_id: string | null;
  /** D-9 起：多目标 fitness（ADR-0020 E1，不允许裸 Sharpe 排序候选） */
  fitness: number | null;
  /**
   * D-9 起：candidate 路径下自动并跑的 buy_and_hold 对照；内置路径为 null。
   * alpha 判定 = fitness 显著高于 baseline.fitness。
   */
  baseline: BaselineSnapshot | null;
  /** D-12 起：holdout 时间切分验证；曲线太短或显式关闭时 null */
  validation?: ValidationBlock | null;
  venue: string;
  symbol: string;
  timeframe: string;
  initial_cash: number;
  final_equity: number;
  total_return_pct: number;
  num_trades: number;
  total_fees: number;
  num_bars_processed: number;
  period_start: string;
  period_end: string;
  sharpe: number | null;
  sortino: number | null;
  /** 最大回撤百分比（正数，cap 100.0）；超 100% 的物理穿仓由 blew_up 表达 */
  max_drawdown_pct: number;
  win_rate: number | null;
  /**
   * D-9 起：账户是否穿仓（任意时点 equity ≤ -1%×initial_cash）。true 表示本次
   * 回测物理上不可信，agent 必须显式告警而非直接展示 Sharpe / 收益率。
   */
  blew_up?: boolean;
  /**
   * D-9 起：回测物理一致性警告（如账户穿仓、现金透支）。非空时禁止无声渲染，
   * orchestrator 必须把警告原样转给用户。
   */
  health_warnings?: string[];
  final_positions: PositionSnapshot[];
};

export type BacktestParams = {
  /** 内置策略 ID（与 candidateId 二选一） */
  strategyId?: string;
  /** D-9 起：LLM 自创策略候选 ID（与 strategyId 二选一） */
  candidateId?: string;
  params?: Record<string, unknown>;
  venue?: string;
  symbol: string;
  timeframe?: string;
  fromTs: string;
  toTs: string;
  initialCash?: number;
  feeRate?: number;
  /** D-8c 起：上游 research 血缘 */
  researchId?: string;
  /** D-8c 起：触发本次回测的 strategy_hint（审计用） */
  strategyHint?: Record<string, unknown>;
};

// ────────────────────────────────────────────────────────────────────
// D-9 · 自创策略候选（ADR-0020 E1 MVP）
// ────────────────────────────────────────────────────────────────────

export type AuthorStrategyParams = {
  /** 完整 Python 源码；服务端跑三道沙盒（ast / dynamic_loader / contract） */
  code: string;
  /** 人话说明策略逻辑 / 适用场景（≤ 2000 字符） */
  description?: string;
  /**
   * 生成时因子血缘（ADR-0047）：策略设计依据的 top 因子 + 衰减态快照。
   * 原样落 strategy_candidates.factor_snapshot（snake_case 已由调用方转换）。
   */
  factorSnapshot?: Record<string, unknown>;
};

export type AuthorStrategyResult = {
  candidate_id: string;
  code_hash: string;
  /** true=新落库；false=撞到同 code_hash，返已有 ID（幂等） */
  created: boolean;
  audit: Record<string, unknown>;
};

export type StrategyCandidateSummary = {
  id: string;
  code_hash: string;
  description: string;
  author: "llm" | "user" | "system";
  status: "candidate" | "rejected" | "promoted";
  metrics: Record<string, unknown> | null;
  fitness: number | null;
  last_backtest_run_id: string | null;
  created_at: string;
  updated_at: string;
};

export type StrategyCandidateRecord = StrategyCandidateSummary & {
  code: string;
  author_id: string | null;
  audit: Record<string, unknown> | null;
};

export type ListCandidatesFilter = {
  status?: "candidate" | "rejected" | "promoted";
  authorId?: string;
  limit?: number;
};

// ────────────────────────────────────────────────────────────────────
// D-8c · compose + lineage 类型
// ────────────────────────────────────────────────────────────────────

export type StrategyHint = {
  family: "trend" | "mean_reversion" | "buy_hold" | "breakout" | "volatility" | "none";
  params: Record<string, unknown>;
  reasoning: string;
};

export type FactorInput = {
  name: string;
  kind: "momentum" | "mean_reversion" | "volatility" | "macro" | "sentiment";
  value: number | string;
  strength: number;
  horizon?: "intraday" | "swing" | "position";
  explanation?: string;
};

export type ComposeStrategyParams = {
  hint: StrategyHint;
  factors?: FactorInput[];
  timeframe?: string;
};

export type ComposeStrategyResult = {
  strategy_id: string | null;
  params: Record<string, unknown>;
  reasoning: string;
  rejected_reason: string | null;
};

export type BacktestRunSummary = {
  run_id: string;
  strategy_code: string;
  params_hash: string | null;
  research_id: string | null;
  config: Record<string, unknown>;
  metrics: Record<string, unknown>;
  strategy_hint: Record<string, unknown> | null;
  status: string;
  created_at: string;
};

export type SubmitOrderParams = {
  venue?: string;
  symbol: string;
  side: "BUY" | "SELL";
  type: "MARKET" | "LIMIT";
  quantity: number;
  price?: number;
  /** D-8a' 后 optional：省略则 paper 服务端调 data /ticker 自取 */
  refPrice?: number;
  feeRate?: number;
};

export type SubmitOrderResult = {
  client_order_id: string;
  venue: string;
  symbol: string;
  side: "BUY" | "SELL";
  order_type: "MARKET" | "LIMIT";
  requested_quantity: number;
  requested_price: number | null;
  status: "FILLED" | "REJECTED";
  filled_quantity: number;
  avg_fill_price: number | null;
  fee: number;
  notional: number;
  rejection_reason: string | null;
  ts_event: string;
};

// ────────────────────────────────────────────────────────────────────
// D-8b plan / query 类型
// ────────────────────────────────────────────────────────────────────

export type TradeIntent = "open_long" | "open_short" | "close" | "rebalance";

export type PlanRecord = {
  plan_id: string;
  account_id?: string | null;
  intent: TradeIntent;
  venue: string;
  symbol: string;
  order_params: {
    side: "BUY" | "SELL";
    type: "MARKET" | "LIMIT";
    quantity: number;
    price?: number;
  };
  risk_params: Record<string, unknown>;
  rationale: string;
  status: "pending_approval" | "approved" | "rejected" | "executed" | "expired";
  approval_token: string | null;
  approved_by: string | null;
  rejection_reason: string | null;
  created_at: string;
  approved_at: string | null;
  executed_at: string | null;
  expire_at: string;
  resulting_order_id: string | null;
};

export type CreatePlanParams = {
  intent: TradeIntent;
  venue?: string;
  symbol: string;
  side: "BUY" | "SELL";
  orderType: "MARKET" | "LIMIT";
  quantity: number;
  price?: number;
  rationale: string;
  expireInSeconds?: number;
};

export type ExecutePlanResult = {
  plan_id: string;
  plan_status: "executed";
  order: SubmitOrderResult;
};

export type OrderRecord = {
  client_order_id: string;
  venue: string | null;
  symbol: string | null;
  side: "BUY" | "SELL";
  type: string;
  quantity: number;
  price: number | null;
  status: string;
  filled_quantity: number;
  avg_fill_price: number | null;
  fee: number | null;
  notional: number | null;
  ts_event: string;
  ts_init: string;
  trade_plan_id: string | null;
};

export type PositionRecord = {
  venue: string;
  symbol: string;
  quantity: number;
  avg_open_price: number;
  realized_pnl: number;
  generation: number;
  updated_at: string;
};

export type AccountSnapshot = {
  account_id: string;
  /** D-11：报告 / 折算目标货币（默认 USD）。 */
  base_currency: string;
  initial_cash: number;
  /** D-11：各币种桶折算到 base_currency 后的总现金。 */
  cash: number;
  /** D-11：折算前的按币种现金桶（如 {"USD": 5000, "USDT": -1000}）。 */
  cash_balances: Record<string, number>;
  positions_value: number;
  total_equity: number;
  realized_pnl: number;
  /** D-11：折算时 FX 不可用 / 偏旧的币种告警；非空时须原样转告用户。 */
  fx_warnings: string[];
  created_at: string;
  updated_at: string;
};

/** D-11 · live runner（issue #1）。 */
export type StrategyRunRecord = {
  id: string;
  candidate_id: string;
  account_id: string;
  status: "running" | "stopped" | "errored";
  venue: string;
  symbol: string;
  timeframe: string;
  params: Record<string, unknown>;
  last_bar_ts: string | null;
  cumulative_pnl: number;
  error_log: Array<Record<string, unknown>>;
  started_at: string;
  stopped_at: string | null;
};

export type StartStrategyParams = {
  candidateId: string;
  /** 必填：按 symbol 的市场分类显式选 venue，不预设 binance（CLAUDE.md §3 全球市场）。 */
  venue: string;
  symbol: string;
  timeframe?: string;
  params?: Record<string, unknown>;
};

/** D-11 · live runner 决策复盘日志一行。 */
export type StrategyRunDecisionRecord = {
  id: string;
  run_id: string;
  bar_ts: string;
  bar_close: number;
  side: "BUY" | "SELL";
  quantity: number;
  order_type: string;
  limit_price: number | null;
  tag: string | null;
  /** 开/平意图（按下单前持仓方向 + side 判），补 side 缺失的做多/做空语义。 */
  intent: "open_long" | "open_short" | "close" | null;
  outcome: "filled" | "rejected" | "risk_rejected";
  fill_price: number | null;
  fee: number | null;
  plan_id: string | null;
  order_id: string | null;
  reason: string | null;
  created_at: string;
};

export class PaperClient {
  private readonly http: HttpClient;

  constructor(options: { baseUrl: string; token: string; timeoutMs?: number }) {
    this.http = new HttpClient(options);
  }

  async health(): Promise<{ status: string; service: string; version: string }> {
    return await this.http.get("/health");
  }

  async listStrategies(): Promise<{ strategies: string[] }> {
    return await this.http.get("/strategies");
  }

  async runBacktest(params: BacktestParams): Promise<BacktestReport> {
    if (!params.strategyId && !params.candidateId) {
      throw new Error(
        "PaperClient.runBacktest: must provide strategyId or candidateId",
      );
    }
    if (params.strategyId && params.candidateId) {
      throw new Error(
        "PaperClient.runBacktest: strategyId and candidateId are mutually exclusive",
      );
    }
    return await this.http.post<BacktestReport>("/backtest", {
      strategy_id: params.strategyId,
      candidate_id: params.candidateId,
      params: params.params ?? {},
      venue: params.venue ?? "binance",
      symbol: params.symbol,
      timeframe: params.timeframe ?? "1h",
      from_ts: params.fromTs,
      to_ts: params.toTs,
      initial_cash: params.initialCash ?? 10_000,
      fee_rate: params.feeRate ?? 0.001,
      research_id: params.researchId,
      strategy_hint: params.strategyHint,
    });
  }

  // ────────────────────────────────────────────────────────────────────
  // D-9 · 自创策略候选（ADR-0020 E1 MVP）
  // ────────────────────────────────────────────────────────────────────

  async authorStrategy(params: AuthorStrategyParams): Promise<AuthorStrategyResult> {
    return await this.http.post<AuthorStrategyResult>("/strategy_candidates", {
      code: params.code,
      description: params.description ?? "",
      factor_snapshot: params.factorSnapshot,
    });
  }

  async getCandidate(candidateId: string): Promise<StrategyCandidateRecord> {
    return await this.http.get<StrategyCandidateRecord>(
      `/strategy_candidates/${candidateId}`,
    );
  }

  async listCandidates(
    filter: ListCandidatesFilter = {},
  ): Promise<StrategyCandidateSummary[]> {
    return await this.http.get<StrategyCandidateSummary[]>(
      "/strategy_candidates",
      {
        status: filter.status,
        author_id: filter.authorId,
        limit: filter.limit,
      },
    );
  }

  /**
   * D-9 · 把候选从 `status='candidate'` 切到 `'promoted'`。
   *
   * 后端校验：候选不存在 → 404；status≠candidate → 409；fitness=null → 400。
   * orchestration tool `paper.promote_candidate` 默认 permission `ask`——agent 调时
   * 会弹气泡让用户在对话里二次确认。
   *
   * @param reason - 为什么 promote（建议含回测区间 / fitness vs baseline / 风控指标）；
   *                 落到候选 `audit.promotion.reason` 便于事后复盘
   */
  async promoteCandidate(
    candidateId: string,
    reason: string,
  ): Promise<StrategyCandidateRecord> {
    return await this.http.post<StrategyCandidateRecord>(
      `/strategy_candidates/${candidateId}/promote`,
      { reason },
    );
  }

  // ────────────────────────────────────────────────────────────────────
  // D-8c · 策略组装 + 历史回测查询
  // ────────────────────────────────────────────────────────────────────

  async composeStrategy(
    params: ComposeStrategyParams,
  ): Promise<ComposeStrategyResult> {
    return await this.http.post<ComposeStrategyResult>("/strategies/compose", {
      hint: params.hint,
      factors: params.factors ?? [],
      timeframe: params.timeframe ?? "1h",
    });
  }

  async listBacktestRuns(filter: {
    researchId?: string;
    strategyCode?: string;
    limit?: number;
  }): Promise<BacktestRunSummary[]> {
    return await this.http.get<BacktestRunSummary[]>("/backtest_runs", {
      research_id: filter.researchId,
      strategy_code: filter.strategyCode,
      limit: filter.limit,
    });
  }

  async submitOrder(params: SubmitOrderParams): Promise<SubmitOrderResult> {
    return await this.http.post<SubmitOrderResult>("/orders/submit", {
      venue: params.venue ?? "binance",
      symbol: params.symbol,
      side: params.side,
      type: params.type,
      quantity: params.quantity,
      price: params.price,
      ref_price: params.refPrice,
      fee_rate: params.feeRate ?? 0.001,
    });
  }

  // ────────────────────────────────────────────────────────────────────
  // D-8b plan/exec API
  // ────────────────────────────────────────────────────────────────────

  async createPlan(params: CreatePlanParams): Promise<PlanRecord> {
    return await this.http.post<PlanRecord>("/plans", {
      intent: params.intent,
      venue: params.venue ?? "binance",
      symbol: params.symbol,
      side: params.side,
      type: params.orderType,
      quantity: params.quantity,
      price: params.price,
      rationale: params.rationale,
      expire_in_seconds: params.expireInSeconds ?? 300,
    });
  }

  async approvePlan(planId: string, approver: string): Promise<PlanRecord> {
    return await this.http.post<PlanRecord>(`/plans/${planId}/approve`, { approver });
  }

  async rejectPlan(planId: string, reason: string, rejector: string): Promise<PlanRecord> {
    return await this.http.post<PlanRecord>(`/plans/${planId}/reject`, { reason, rejector });
  }

  async executePlan(planId: string, approvalToken: string): Promise<ExecutePlanResult> {
    return await this.http.post<ExecutePlanResult>(`/plans/${planId}/execute`, {
      approvalToken,
    });
  }

  async getPlan(planId: string): Promise<PlanRecord> {
    return await this.http.get<PlanRecord>(`/plans/${planId}`);
  }

  async listPlans(filter?: { status?: string; limit?: number }): Promise<PlanRecord[]> {
    return await this.http.get<PlanRecord[]>("/plans", {
      status: filter?.status,
      limit: filter?.limit,
    });
  }

  // ────────────────────────────────────────────────────────────────────
  // D-8b 查询
  // ────────────────────────────────────────────────────────────────────

  async listOrders(filter?: {
    symbol?: string;
    status?: string;
    limit?: number;
  }): Promise<OrderRecord[]> {
    return await this.http.get<OrderRecord[]>("/orders", {
      symbol: filter?.symbol,
      status: filter?.status,
      limit: filter?.limit,
    });
  }

  async listPositions(includeFlat = false): Promise<PositionRecord[]> {
    return await this.http.get<PositionRecord[]>("/positions", {
      include_flat: includeFlat,
    });
  }

  async getAccount(): Promise<AccountSnapshot> {
    return await this.http.get<AccountSnapshot>("/accounts/me");
  }

  // ────────────────────────────────────────────────────────────────────
  // D-11 · live runner（issue #1）
  // ────────────────────────────────────────────────────────────────────

  async startStrategy(params: StartStrategyParams): Promise<StrategyRunRecord> {
    return await this.http.post<StrategyRunRecord>("/strategy_runs", {
      candidate_id: params.candidateId,
      venue: params.venue,
      symbol: params.symbol,
      timeframe: params.timeframe ?? "1h",
      params: params.params ?? {},
    });
  }

  async stopStrategy(runId: string): Promise<StrategyRunRecord> {
    return await this.http.post<StrategyRunRecord>(`/strategy_runs/${runId}/stop`, {});
  }

  async listStrategyRuns(filter?: { status?: string }): Promise<StrategyRunRecord[]> {
    return await this.http.get<StrategyRunRecord[]>("/strategy_runs", {
      status: filter?.status,
    });
  }

  async listStrategyRunDecisions(
    runId: string,
    limit?: number,
  ): Promise<StrategyRunDecisionRecord[]> {
    return await this.http.get<StrategyRunDecisionRecord[]>(
      `/strategy_runs/${runId}/decisions`,
      { limit },
    );
  }
}
