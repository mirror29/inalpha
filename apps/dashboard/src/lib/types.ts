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
