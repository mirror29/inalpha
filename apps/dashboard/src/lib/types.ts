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
  /** 这笔成交的已实现盈亏(毛口径):开仓/加仓单为 0,平/减仓单为实现盈亏;未成交为 null。 */
  realized_pnl: number | null;
  ts_event: string;
  ts_init: string;
  trade_plan_id: string | null;
}

/** 运行日志级别 —— 与后端 live_runner 写入一致。 */
export type RunLogLevel = "info" | "warn" | "error";

/** run_log 一条 —— 运行日志(info 活动 / warn 可恢复 / error 终态)。 */
export interface RunLogEntry {
  ts: string;
  level: RunLogLevel;
  msg: string;
  code: string | null;
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
  /** 运行日志(滚动窗口,最近 N 条):起跑 / 出单 / 停止 / 退避 / 错误。 */
  run_log: RunLogEntry[];
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
  /** 策略池快照(后端按 fitness DESC 排序,总览只取前若干条);失败降级为空。 */
  candidates: StrategyCandidateSummary[];
  /** 策略池按 status 计数(总数取自后端原始集合,非截断后的)。 */
  candidateCounts: { all: number; promoted: number; candidate: number };
  /** orders 命中上限被截断(还有更早的订单未显示) —— UI 给「仅显示最近 N」提示,不静默。 */
  ordersTruncated: boolean;
  /** server 侧采集这一帧的时刻(ISO);UI 显示 "数据时间"。 */
  asOf: string;
}

/** GET /api/runners —— Live Runner 列表页负载。 */
export interface RunnersPayload {
  runs: StrategyRunRecord[];
  runningCount: number;
  /** runs 命中上限被截断(还有更早的 run 未显示) —— UI 给截断提示,不静默。 */
  truncated: boolean;
  asOf: string;
}

/** GET /api/runners/[id] —— 单个 run 详情 + 决策时间线。 */
export interface RunDetailPayload {
  /** 在 list 里按 id 找到的 run;不存在为 null。 */
  run: StrategyRunRecord | null;
  decisions: StrategyRunDecisionRecord[];
  /** 该 run 所跑的策略候选摘要(用 run.candidate_id 反查);拿不到为 null,UI 退化为只显 id。 */
  candidate: StrategyCandidateSummary | null;
  asOf: string;
}

/** 一根 K 线(data /bars 的 BarResponse 子集)。 */
export interface BarPoint {
  ts: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

/** GET /api/bars —— K 线负载(给 Live Runner 详情叠图)。 */
export interface BarsPayload {
  venue: string;
  symbol: string;
  timeframe: string;
  bars: BarPoint[];
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
  /** candidates 命中上限被截断(还有更多候选未显示) —— UI 给截断提示,不静默。 */
  truncated: boolean;
  asOf: string;
}

/** 该候选最近一次回测的概要(回测时间 / 区间)。 */
export interface BacktestRunSummary {
  runId: string;
  /** 回测落库时刻(= 跑于)。 */
  createdAt: string;
  /** 回测区间(config.from_ts / to_ts)；拿不到为 null。 */
  periodStart: string | null;
  periodEnd: string | null;
  venue: string | null;
  symbol: string | null;
  timeframe: string | null;
  /** 该次回测的指标字典(backtest_runs.metrics,含专业级扩展指标;可 null)。 */
  metrics: Record<string, number | null> | null;
}

/** GET /backtest_runs/{id}/trades 一行 —— 回测逐笔成交(含每笔实现盈亏)。 */
export interface BacktestTradeRecord {
  seq: number;
  bar_ts: string;
  bar_close: number;
  side: "BUY" | "SELL";
  quantity: number;
  order_type: string;
  fill_price: number | null;
  fee: number | null;
  /** 本笔成交实现盈亏(开仓笔=0,平仓/反手笔为价差盈亏,不含手续费)。 */
  realized_pnl: number | null;
  intent: "open_long" | "open_short" | "close" | null;
  tag: string | null;
}

/** GET /api/backtests/[runId] —— 单次回测详情(活动流点击回测事件的落地页)。 */
export interface BacktestRunDetailPayload {
  run:
    | (BacktestRunSummary & {
        strategyCode: string;
        status: string;
        /** candidate 回测时非空 → 可跳策略详情;内置策略为 null。 */
        candidateId: string | null;
        /** 候选描述(标题可读);拿不到为 null,退化显示 strategyCode。 */
        candidateDescription: string | null;
      })
    | null;
  trades: BacktestTradeRecord[];
  asOf: string;
}

/** GET /api/lab/[id] —— 候选详情。 */
export interface CandidateDetailPayload {
  candidate: StrategyCandidateRecord | null;
  /** 该候选派生的 live runner(按 started_at 倒序);用于「执行记录 / K 线 / 历史交易」。 */
  runs: StrategyRunRecord[];
  /** 最近一个 run 的决策(K 线叠加 + 历史交易表用);无 run 时为空。 */
  latestRunDecisions: StrategyRunDecisionRecord[];
  /** 最近一次回测概要(回测时间/区间);拿不到为 null。 */
  backtestRun: BacktestRunSummary | null;
  /** 最近一次回测的逐笔成交(含每笔实现盈亏);无回测/无成交为空。 */
  backtestTrades: BacktestTradeRecord[];
  asOf: string;
}

// ── ⑤ 风控面板 ──

/** GET /risk/rules 的一条规则。 */
export interface RiskRule {
  name: string;
  short_desc: string;
}

/** GET /risk/locks 的一把活跃锁。 */
export interface RiskLock {
  id: number;
  scope: string;
  market: string | null;
  symbol: string | null;
  side: string;
  rule_name: string;
  reason: string;
  locked_at: string;
  locked_until: string;
}

/** 一条风控事件 —— 归一自「历史锁」与「跨 run 被风控拒的下单」。 */
export interface RiskEvent {
  /** 稳定去重 id:`lock:<id>` / `rej:<decisionId>`。 */
  id: string;
  /** lock=触发了一把锁;rejection=一笔下单被风控拦(可能没产生锁)。 */
  kind: "lock" | "rejection";
  /** 事件时点(lock=locked_at;rejection=bar_ts)。 */
  ts: string;
  /** 命中的风控规则名(拒单从 reason 里解析 `[RuleName]`,解析不到给 "risk")。 */
  rule: string;
  /** global / market / symbol(拒单恒 symbol)。 */
  scope: string;
  /** 人类可读对象:market/symbol 或 run 的 symbol。 */
  label: string;
  reason: string;
  /** lock:active/expired/unlocked;rejection:rejected。 */
  status: "active" | "expired" | "unlocked" | "rejected";
  /** lock 的解锁时点;rejection 为 null。 */
  until: string | null;
  /** rejection 跳到对应 run;lock 为 null。 */
  href: string | null;
}

/** GET /api/risk —— 风控面板负载(规则 + 活跃锁 + 最近风控事件)。 */
export interface RiskPayload {
  /** 风控是否启用(rules 配置)。 */
  enabled: boolean;
  starting_balance: number;
  rules: RiskRule[];
  /** 当前生效锁(实时拦截视图)。 */
  locks: RiskLock[];
  /** 最近风控事件(历史锁 + 跨 run 被拒决策),按时间倒序。 */
  events: RiskEvent[];
  sources: { rules: boolean; locks: boolean; history: boolean; rejections: boolean };
  asOf: string;
}

// ── ⑥ 因子库 ──

/** GET /factor/catalog 的一个因子定义(静态目录)。 */
export interface FactorSpec {
  factor_id: string;
  source: string;
  name: string;
  kind: string;
  needs_universe: boolean;
  direction_hint: number;
  available: boolean;
}

/** POST /factor/snapshot 的一条有效性记录(运行时计算)。 */
export interface FactorEffectiveness {
  factor_id: string;
  source: string;
  name: string;
  kind: string;
  value: number | null;
  /** 时序 Rank IC(spearman(rank(factor), rank(fwd_return)))。 */
  rank_ic: number;
  /** 近 1/3 样本窗的 Rank IC —— 与 rank_ic 同号且量级接近≈稳定,反号/趋零≈正在衰减。 */
  rank_ic_recent: number;
  /** 因子换手:1 - spearman(f_t, f_{t-1}),0≈信号几乎不动;高 IC + 高换手应打折。 */
  turnover: number;
  /** IC 信息比(分段 IC 均值/标准差),稳定性。 */
  icir: number;
  /** 择时方向 +1/-1/0(sign(rank_ic),过阈才非 0)。 */
  direction: number;
  /** |rank_ic| 归一到 0-1。 */
  strength: number;
  sample_size: number;
  long_short_return: number;
  /** 样本不足标记。 */
  low_confidence: boolean;
}

/** GET /api/factors —— 因子库面板:目录 + 当前标的有效性快照。 */
export interface FactorsPayload {
  catalog: FactorSpec[];
  /** 各源是否可用(qlib 默认关)。 */
  sources: Record<string, boolean>;
  /** 当前标的的有效因子排行(snapshot);取不到为 null。 */
  effectiveness: {
    venue: string;
    symbol: string;
    timeframe: string;
    available: boolean;
    reason: string | null;
    bars_used: number;
    as_of: string | null;
    top_factors: FactorEffectiveness[];
  } | null;
  /** catalog 是否取到(factor 服务可能没起)。 */
  catalogOk: boolean;
  asOf: string;
}

// ── ③ Agent 运行日志 / 可观测性 ──

/** 统一活动流的事件类型(跨模块归一)。 */
export type ActivityKind =
  | "scheduler"
  | "permission"
  | "decision"
  | "risk"
  | "order"
  | "backtest"
  | "runner"
  | "conversation";

export type ActivityTone = "bull" | "fox" | "gold" | "cyan" | "muted";

/** 行内迷你指标 chip(回测的 fitness/收益、订单的方向/盈亏)—— 扫读定位用。 */
export interface ActivityStat {
  text: string;
  tone?: ActivityTone;
}

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
  /** 行内迷你指标(可选):紧跟标题,带语调染色,扫一眼能定位关键数字。 */
  stats?: ActivityStat[];
}

/** GET /api/activity —— Agent 活动流聚合负载。 */
export interface ActivityPayload {
  events: ActivityEvent[];
  /** 各模块事件计数(过滤器角标用)。 */
  counts: Record<ActivityKind, number>;
  schedulerRunning: boolean;
  pendingCount: number;
  activeLockCount: number;
  /** 决策 fan-out 只覆盖最近 N 个 run;为 true 表示更早 run 的决策事件未纳入(不静默)。 */
  decisionsTruncated: boolean;
  /** 每个数据源是否取到(取不到 → UI 标"该源不可用",不静默当作空)。 */
  sources: {
    scheduler: boolean;
    permissions: boolean;
    risk: boolean;
    runs: boolean;
    orders: boolean;
    backtests: boolean;
    conversations: boolean;
  };
  asOf: string;
}
