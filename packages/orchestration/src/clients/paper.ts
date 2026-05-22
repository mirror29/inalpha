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

export type BacktestReport = {
  /** D-8c 起：落库后 run_id，可作血缘锚点供 trade.create_plan 引用 */
  run_id: string | null;
  /** D-8c 起：上游 research 血缘（透传） */
  research_id: string | null;
  /** D-8c 起：sha256(strategy_code|params) 前 16 hex，去重用 */
  params_hash: string | null;

  strategy_id: string;
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
  max_drawdown_pct: number;
  win_rate: number | null;
  final_positions: PositionSnapshot[];
};

export type BacktestParams = {
  strategyId: string;
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
// D-8c · compose + lineage 类型
// ────────────────────────────────────────────────────────────────────

export type StrategyHint = {
  family: "trend" | "mean_reversion" | "buy_hold" | "none";
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
  initial_cash: number;
  cash: number;
  positions_value: number;
  total_equity: number;
  realized_pnl: number;
  created_at: string;
  updated_at: string;
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
    return await this.http.post<BacktestReport>("/backtest", {
      strategy_id: params.strategyId,
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
}
