/**
 * 后端 paper / data service schema 的 TS 镜像。
 * 源:services/paper/src/inalpha_paper/schemas.py + data service /ticker。
 * 只镜像看板用到的字段,保持精简;字段语义与后端一致。
 */

/** GET /accounts/me —— 账户快照(D-11 多币种,已折算到 base_currency)。 */
export interface AccountSnapshot {
  account_id: string;
  base_currency: string;
  initial_cash: number;
  /** 各币种桶折算到 base_currency 后的总现金。 */
  cash: number;
  /** 折算前的按币种现金桶,如 {"USD": 5000, "USDT": -1000}。 */
  cash_balances: Record<string, number>;
  positions_value: number;
  total_equity: number;
  realized_pnl: number;
  /** 非空 → FX 折算不完整,UI 必须显式告警。 */
  fx_warnings: string[];
  created_at: string;
  updated_at: string;
}

/** GET /positions 元素。 */
export interface PositionRecord {
  venue: string;
  symbol: string;
  quantity: number;
  avg_open_price: number;
  realized_pnl: number;
  generation: number;
  currency: string | null;
  updated_at: string;
}

/** GET /orders 元素。 */
export interface OrderRecord {
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
}

/** GET /strategy_runs 元素(live runner 运行态)。 */
export interface StrategyRunRecord {
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
}

/** GET /strategy_runs/{id}/decisions 元素 —— 决策复盘时间线一行。 */
export interface StrategyRunDecisionRecord {
  id: string;
  run_id: string;
  bar_ts: string;
  bar_close: number;
  side: "BUY" | "SELL";
  quantity: number;
  order_type: string;
  limit_price: number | null;
  /** 策略经 Order.tag 透传的语义意图。 */
  tag: string | null;
  /** 按持仓方向 + side 判的开/平意图。 */
  intent: "open_long" | "open_short" | "close" | null;
  outcome: "filled" | "rejected" | "risk_rejected";
  fill_price: number | null;
  fee: number | null;
  plan_id: string | null;
  order_id: string | null;
  /** 拒单原因(风控 / 其他);outcome 非 filled 时通常有值。 */
  reason: string | null;
  created_at: string;
}

/** data service GET /ticker —— 最新价 + 新鲜度。 */
export interface TickerResponse {
  venue: string;
  symbol: string;
  price: number;
  ts: string;
  source: string;
  is_stale: boolean;
  stale_seconds: number;
}

/** 持仓行 + BFF 补的最新价(best-effort)。 */
export interface PositionWithMark extends PositionRecord {
  /** 最新价;data /ticker 拿不到时为 null。 */
  mark_price: number | null;
  /** 最新价是否过时(超 freshness 阈值)。 */
  mark_stale: boolean;
  /** 浮动盈亏 = (mark - avg_open) * qty;mark 缺失时为 null。 */
  unrealized_pnl: number | null;
}

/** GET /api/overview —— BFF 聚合后的整页负载。 */
export interface OverviewPayload {
  account: AccountSnapshot;
  positions: PositionWithMark[];
  orders: OrderRecord[];
  runs: StrategyRunRecord[];
  activeRunnerCount: number;
  /** server 侧采集这一帧的时刻(ISO);UI 显示 "数据时间"。 */
  asOf: string;
}

/** GET /api/runners —— Live Runner 列表页负载。 */
export interface RunnersPayload {
  runs: StrategyRunRecord[];
  runningCount: number;
  asOf: string;
}

/** GET /api/runners/[id] —— 单个 run 详情 + 决策时间线。 */
export interface RunDetailPayload {
  /** 在 list 里按 id 找到的 run;不存在为 null。 */
  run: StrategyRunRecord | null;
  decisions: StrategyRunDecisionRecord[];
  asOf: string;
}

// ── ④ 策略实验室 + 回测史 ──

/** GET /strategy_candidates 列表元素(不含 code)。metrics 由最近一次回测写入。 */
export interface StrategyCandidateSummary {
  id: string;
  code_hash: string;
  description: string;
  author: "llm" | "user" | "system";
  status: "candidate" | "rejected" | "promoted";
  /** 最近回测的指标(键随策略路径变化:sharpe/sortino/calmar/win_rate/
   *  num_trades/max_drawdown_pct/total_return_pct…);未回测为 null。 */
  metrics: Record<string, number> | null;
  /** 多目标适应度(ADR-0020);裸 Sharpe 排序不可用。null = 未回测。 */
  fitness: number | null;
  last_backtest_run_id: string | null;
  created_at: string;
  updated_at: string;
}

/** GET /strategy_candidates/{id} 完整记录(含源码 + 审计)。 */
export interface StrategyCandidateRecord extends StrategyCandidateSummary {
  code: string;
  author_id: string | null;
  owner_account_id: string | null;
  audit: Record<string, unknown> | null;
}

/** GET /api/lab —— 候选列表页负载。 */
export interface LabPayload {
  /** 后端已按 fitness DESC 排序。 */
  candidates: StrategyCandidateSummary[];
  counts: { all: number; promoted: number; candidate: number; rejected: number };
  asOf: string;
}

/** GET /api/lab/[id] —— 候选详情。 */
export interface CandidateDetailPayload {
  candidate: StrategyCandidateRecord | null;
  asOf: string;
}

// ── ③ Agent 运行日志 / 可观测性 ──

/** 统一活动流的事件类型(跨模块归一)。 */
export type ActivityKind =
  | "scheduler"
  | "permission"
  | "decision"
  | "risk"
  | "order";

export type ActivityTone = "bull" | "fox" | "gold" | "cyan" | "muted";

/** 一条归一化的 agent 活动事件。 */
export interface ActivityEvent {
  id: string;
  kind: ActivityKind;
  /** ISO 时间,用于排序与显示。 */
  ts: string;
  /** 一句话标题(标的 / job / tool / 规则名)。 */
  title: string;
  /** 补充明细(状态 / 原因 / 触发方式)。 */
  detail: string | null;
  /** 结果标签(success/failed/filled/risk_rejected/pending…);无则 null。 */
  outcome: string | null;
  tone: ActivityTone;
  /** 可点进的目标(如 runner 详情);无则 null。 */
  href: string | null;
}

/** GET /api/activity —— Agent 活动流聚合负载。 */
export interface ActivityPayload {
  events: ActivityEvent[];
  /** 各模块事件计数(过滤器角标用)。 */
  counts: Record<ActivityKind, number>;
  schedulerRunning: boolean;
  pendingCount: number;
  activeLockCount: number;
  /** 每个数据源是否取到(取不到 → UI 标"该源不可用",不静默当作空)。 */
  sources: {
    scheduler: boolean;
    permissions: boolean;
    risk: boolean;
    runs: boolean;
    orders: boolean;
  };
  asOf: string;
}
