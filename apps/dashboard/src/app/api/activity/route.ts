import { NextResponse } from "next/server";

import { backendFetch } from "@/lib/backend";
import { listChatThreads } from "@/lib/mastra";
import type {
  ActivityEvent,
  ActivityKind,
  ActivityPayload,
  ActivityStat,
  ActivityTone,
  OrderRecord,
  StrategyRunDecisionRecord,
  StrategyRunRecord,
} from "@/lib/types";

export const dynamic = "force-dynamic";

/** 聚合时的上限,避免跨服务 fan-out 失控。 */
const MAX_EVENTS = 120;
/** 公平截断:每类事件保底保留的最近条数(防高频源把低频源整类挤出窗口)。 */
const PER_KIND_KEEP = 10;
const DECISIONS_PER_RUN = 50;
const MAX_RUNS_FOR_DECISIONS = 8;
const ORDERS_LIMIT = 40;

// ── 各数据源的原始响应(只声明用到的字段)──
interface SchedulerRunsResp {
  runs: Array<{
    runId: string;
    jobId: string;
    scheduledAt: string;
    startedAt: string;
    finishedAt: string | null;
    status: "running" | "success" | "failed" | "timeout";
    trigger: "cron" | "manual";
    error: unknown;
  }>;
}
interface SchedulerJobsResp {
  schedulerRunning: boolean;
}
interface PendingResp {
  pending: Array<{ requestId: string; toolName: string; createdAt: string }>;
}
/** /permissions/history 一行 —— 审批审计终态(mastra Postgres,重启不丢)。 */
interface ApprovalHistoryResp {
  history: Array<{
    requestId: string;
    toolName: string;
    status: "pending" | "allowed" | "denied" | "expired_timeout" | "expired_restart";
    via: "user" | "timeout" | "restart" | null;
    createdAt: string;
    resolvedAt: string | null;
  }>;
}
/** /backtest_runs 一行(只声明活动流用到的字段)。 */
interface BacktestRunRow {
  run_id: string;
  strategy_code: string;
  status: string;
  created_at: string;
  config: Record<string, unknown>;
  metrics: Record<string, unknown> | null;
}
/** /risk/locks/history 一行 —— 含已过期/已解锁(比 active 锁多 active 字段)。 */
interface RiskLocksResp {
  locks: Array<{
    id: number;
    scope: string;
    market: string | null;
    symbol: string | null;
    side: string;
    rule_name: string;
    reason: string;
    locked_at: string;
    locked_until: string;
    active: boolean;
  }>;
}

/**
 * GET /api/activity —— 跨模块 agent 活动流。
 *
 * 把 scheduler runs / 待审批 / 风控锁 / runner 生命周期与决策 / 订单 / 回测 归一成 ActivityEvent[],
 * 按时间倒序合并。每个源独立 try —— 任一不可用(尤其 mastra:4111 可能没起)只标记
 * sources.<x>=false,不拖垮整页;绝不把"取不到"静默当成"没有"。
 */
export async function GET() {
  const sources = {
    scheduler: true,
    permissions: true,
    // 审批历史是独立后端端点(/permissions/history):它挂了不该把实时 pending
    // 源也标坏 —— 两个 key 分开,避免后写覆盖前写(pending 成功仍被静默标 false)。
    permissionsHistory: true,
    risk: true,
    runs: true,
    orders: true,
    backtests: true,
    conversations: true,
  };
  const events: ActivityEvent[] = [];
  let schedulerRunning = false;
  let pendingCount = 0;
  let activeLockCount = 0;

  // mastra(scheduler / permissions):dev 端不需要 JWT,auth:false。
  const [
    jobsR,
    runsR,
    pendingR,
    approvalHistoryR,
    locksR,
    strategyRunsR,
    ordersR,
    backtestsR,
    threadsR,
  ] = await Promise.allSettled([
      backendFetch<SchedulerJobsResp>("mastra", "/scheduler/jobs", {
        auth: false,
        timeoutMs: 5000,
      }),
      backendFetch<SchedulerRunsResp>("mastra", "/scheduler/runs", {
        auth: false,
        query: { limit: 50 },
        timeoutMs: 5000,
      }),
      backendFetch<PendingResp>("mastra", "/permissions/pending", {
        auth: false,
        timeoutMs: 5000,
      }),
      // 审批审计历史(终态)—— 决策/超时/重启扫尾后仍可回看,不再"决策即消失"。
      backendFetch<ApprovalHistoryResp>("mastra", "/permissions/history", {
        auth: false,
        query: { limit: 20 },
        timeoutMs: 5000,
      }),
      // 历史锁(含已过期)—— 短时效锁过期后仍留在活动流,不静默消失;
      // activeLockCount 仍按「当前生效」从中算。
      backendFetch<RiskLocksResp>("paper", "/risk/locks/history", {
        query: { limit: 50 },
      }),
      // 只为"跨 run 决策"取最近 N 条 —— 上限推给服务端(按 started_at DESC),
      // 这是 8s 轮询热路径,别随 run 历史累积每次拉全量。多探 1 条(N+1)用来判断
      // 「是否还有更早的 run 被跳过」,以便标 decisionsTruncated(不静默)。
      backendFetch<StrategyRunRecord[]>("paper", "/strategy_runs", {
        query: { limit: MAX_RUNS_FOR_DECISIONS + 1 },
      }),
      backendFetch<OrderRecord[]>("paper", "/orders", {
        query: { limit: ORDERS_LIMIT },
      }),
      // 回测史(无 filter = 全局最近 N 条)—— agent 跑的策略回测进活动流。
      backendFetch<BacktestRunRow[]>("paper", "/backtest_runs", {
        query: { limit: 30 },
      }),
      // 用户对话(mastra memory threads)—— 让"发起了什么会话"进入活动流。
      // 8s 轮询热路径:跳过标题回填(下方用 `#id` 兜底),回填只在历史下拉里做。
      listChatThreads(200, { backfillTitles: false }),
    ]);

  // ── scheduler ──
  if (jobsR.status === "fulfilled") schedulerRunning = jobsR.value.schedulerRunning;
  else sources.scheduler = false;
  if (runsR.status === "fulfilled") {
    for (const r of runsR.value.runs) {
      events.push({
        id: `sched:${r.runId}`,
        kind: "scheduler",
        ts: r.finishedAt ?? r.startedAt ?? r.scheduledAt,
        title: r.jobId,
        detail: `${r.trigger} · ${r.status}`,
        outcome: r.status,
        tone:
          r.status === "success"
            ? "bull"
            : r.status === "running"
              ? "cyan"
              : "fox",
        href: null,
      });
    }
  } else {
    sources.scheduler = false;
  }

  // ── permissions(待审批)──
  if (pendingR.status === "fulfilled") {
    pendingCount = pendingR.value.pending.length;
    for (const p of pendingR.value.pending) {
      events.push({
        id: `perm:${p.requestId}`,
        kind: "permission",
        ts: p.createdAt,
        title: p.toolName,
        detail: "awaiting approval",
        outcome: "pending",
        tone: "gold",
        href: null,
      });
    }
  } else {
    sources.permissions = false;
  }

  // ── permissions(审批终态历史)——
  // 与上面"待审批"同 kind/同 id 前缀:挂起期间由内存 pending 源展示(有实时 deadline),
  // 这里只映射终态行(status=pending 跳过,避免同一事件双条);挂起被消费后自然切换成终态。
  if (approvalHistoryR.status === "fulfilled") {
    const detailByStatus: Record<string, string> = {
      allowed: "approved by user",
      denied: "denied by user",
      expired_timeout: "expired (timeout)",
      expired_restart: "expired (server restart)",
    };
    for (const h of approvalHistoryR.value.history) {
      if (h.status === "pending") continue;
      events.push({
        id: `perm:${h.requestId}`,
        kind: "permission",
        ts: h.resolvedAt ?? h.createdAt,
        title: h.toolName,
        detail: detailByStatus[h.status] ?? h.status,
        outcome: h.status,
        tone: h.status === "allowed" ? "bull" : h.status === "denied" ? "fox" : "cyan",
        href: null,
      });
    }
  } else {
    sources.permissionsHistory = false;
  }

  // ── 风控锁(历史:含已过期/已解锁,事件不随过期消失)──
  if (locksR.status === "fulfilled") {
    const nowMs = Date.now();
    for (const l of locksR.value.locks) {
      const isActive = l.active && new Date(l.locked_until).getTime() > nowMs;
      if (isActive) activeLockCount += 1;
      const scopeLabel = [l.market, l.symbol].filter(Boolean).join(" ") || l.scope;
      events.push({
        id: `lock:${l.id}`,
        kind: "risk",
        ts: l.locked_at,
        title: l.rule_name,
        detail: `${scopeLabel} · ${l.reason}`,
        // 生效中=locked(红);已过期/解锁=保留可查但弱化(灰)。
        outcome: isActive ? "locked" : "expired",
        tone: isActive ? "fox" : "muted",
        href: null,
      });
    }
  } else {
    sources.risk = false;
  }

  // ── runner 决策(跨 run,bounded fan-out)──
  let runs: StrategyRunRecord[] = [];
  if (strategyRunsR.status === "fulfilled") {
    runs = strategyRunsR.value;
  } else {
    sources.runs = false;
  }
  // 决策 fan-out 只覆盖最近 N 个 run。上面多探了 1 条(limit=N+1):拿到 >N 条即
  // 说明还有更早的 run 未纳入决策流 —— 标 truncated,UI 给提示,不静默。
  const decisionsTruncated = runs.length > MAX_RUNS_FOR_DECISIONS;
  const recentRuns = [...runs]
    .sort((a, b) => +new Date(b.started_at) - +new Date(a.started_at))
    .slice(0, MAX_RUNS_FOR_DECISIONS);
  const decisionsByRun = await Promise.allSettled(
    recentRuns.map((run) =>
      backendFetch<StrategyRunDecisionRecord[]>(
        "paper",
        `/strategy_runs/${run.id}/decisions`,
        { query: { limit: DECISIONS_PER_RUN } },
      ).then((decs) => ({ run, decs })),
    ),
  );
  for (const r of decisionsByRun) {
    if (r.status !== "fulfilled") continue;
    const { run, decs } = r.value;
    for (const d of decs) {
      events.push({
        id: `dec:${d.id}`,
        kind: "decision",
        ts: d.bar_ts,
        title: `${run.symbol} · ${d.intent ?? d.side}`,
        detail: d.reason ?? `${d.side} ${d.quantity}`,
        outcome: d.outcome,
        tone:
          d.outcome === "filled"
            ? "bull"
            : d.outcome === "risk_rejected"
              ? "fox"
              : "gold",
        href: `/runners/${run.id}`,
      });
    }
  }

  // ── 模拟盘生命周期(启动 / 停止 / 报错)—— 从 strategy_runs 派生,无需新接口。
  // 覆盖范围与决策 fan-out 相同(最近 N 个 run);停止/报错事件带当次盈亏 chip。
  for (const run of recentRuns) {
    const inst = `${run.symbol} · ${run.timeframe}`;
    events.push({
      id: `runstart:${run.id}`,
      kind: "runner",
      ts: run.started_at,
      title: inst,
      detail: `run ${run.id.slice(0, 8)}`,
      outcome: "started",
      tone: "cyan",
      href: `/runners/${run.id}`,
    });
    if (run.stopped_at) {
      const pnl = run.cumulative_pnl;
      events.push({
        id: `runstop:${run.id}`,
        kind: "runner",
        ts: run.stopped_at,
        title: inst,
        detail: `run ${run.id.slice(0, 8)}`,
        outcome: run.status === "errored" ? "errored" : "stopped",
        tone: run.status === "errored" ? "fox" : "muted",
        href: `/runners/${run.id}`,
        stats:
          pnl !== 0
            ? [
                {
                  text: `${pnl > 0 ? "+" : ""}${pnl.toFixed(2)}`,
                  tone: pnl > 0 ? "bull" : "fox",
                },
              ]
            : undefined,
      });
    }
  }

  // ── 订单流 ──
  if (ordersR.status === "fulfilled") {
    for (const o of ordersR.value) {
      const ordStats: ActivityStat[] = [
        { text: o.side, tone: o.side === "BUY" ? "bull" : "fox" },
      ];
      if (o.filled_quantity > 0 && o.avg_fill_price !== null) {
        ordStats.push({
          text: `${o.filled_quantity} @ ${o.avg_fill_price}`,
          tone: "muted",
        });
      }
      if (o.realized_pnl !== null && o.realized_pnl !== 0) {
        ordStats.push({
          text: `${o.realized_pnl > 0 ? "+" : ""}${o.realized_pnl.toFixed(2)}`,
          tone: o.realized_pnl > 0 ? "bull" : "fox",
        });
      }
      events.push({
        id: `ord:${o.client_order_id}`,
        kind: "order",
        ts: o.ts_event,
        title: o.symbol ?? "—",
        detail: `${o.type} · ${o.status}`,
        outcome: o.status,
        tone: orderTone(o.status),
        href: null,
        stats: ordStats,
      });
    }
  } else {
    sources.orders = false;
  }

  // ── 策略回测(agent 跑的 backtest)──
  if (backtestsR.status === "fulfilled") {
    for (const b of backtestsR.value) {
      const symbol = typeof b.config.symbol === "string" ? b.config.symbol : "—";
      const tf = typeof b.config.timeframe === "string" ? b.config.timeframe : "";
      // candidate 回测 → 可点进策略详情。config.candidate_id 为主,strategy_code
      // 的 "candidate:<uuid>" 前缀兜底(防老数据 config 缺字段);内置策略无详情页。
      const candidateId =
        typeof b.config.candidate_id === "string"
          ? b.config.candidate_id
          : b.strategy_code.startsWith("candidate:")
            ? b.strategy_code.slice("candidate:".length)
            : null;
      const fitness =
        typeof b.metrics?.fitness === "number" ? b.metrics.fitness : null;
      const trades =
        typeof b.metrics?.num_trades === "number" ? b.metrics.num_trades : null;
      const retPct =
        typeof b.metrics?.total_return_pct === "number"
          ? b.metrics.total_return_pct
          : null;
      const btStats: ActivityStat[] = [];
      if (fitness !== null) {
        btStats.push({
          text: `fit ${fitness > 0 ? "+" : ""}${fitness.toFixed(2)}`,
          tone: fitness > 0 ? "bull" : fitness < 0 ? "fox" : "muted",
        });
      }
      if (retPct !== null) {
        btStats.push({
          text: `${retPct > 0 ? "+" : ""}${retPct.toFixed(2)}%`,
          tone: retPct > 0 ? "bull" : retPct < 0 ? "fox" : "muted",
        });
      }
      if (trades !== null) btStats.push({ text: `${trades} tr`, tone: "muted" });
      events.push({
        id: `bt:${b.run_id}`,
        kind: "backtest",
        ts: b.created_at,
        title: `${symbol}${tf ? ` · ${tf}` : ""}`,
        detail: b.strategy_code,
        outcome: b.status,
        tone: b.status === "done" ? "cyan" : "fox",
        stats: btStats,
        // 候选回测 → 策略详情(信息全);内置策略回测 → 通用回测详情页。
        href: candidateId ? `/lab/${candidateId}` : `/backtests/${b.run_id}`,
      });
    }
  } else {
    sources.backtests = false;
  }

  // ── 用户对话(会话发起 / 最近活跃)──
  if (threadsR.status === "fulfilled") {
    for (const th of threadsR.value) {
      events.push({
        id: `conv:${th.id}`,
        kind: "conversation",
        ts: th.updatedAt || th.createdAt,
        title: th.title?.trim() || `#${th.id.slice(0, 6)}`,
        detail: null,
        outcome: null,
        tone: "cyan",
        href: null,
      });
    }
  } else {
    sources.conversations = false;
  }

  // 按时间倒序合并 + **公平截断**:纯按时间截前 N 条时,高频源(回测批跑/决策)
  // 会把低频但重要的源(风控锁/审批)整类挤出窗口 —— "风控日志不见了"。
  // 先保每类最近 PER_KIND_KEEP 条,再按时间补满到 MAX_EVENTS。
  events.sort((a, b) => +new Date(b.ts) - +new Date(a.ts));
  const kindKept = new Map<ActivityKind, number>();
  const guaranteed = new Set<string>();
  for (const e of events) {
    const n = kindKept.get(e.kind) ?? 0;
    if (n < PER_KIND_KEEP) {
      kindKept.set(e.kind, n + 1);
      guaranteed.add(e.id);
    }
  }
  const trimmed: ActivityEvent[] = [];
  let fillBudget = Math.max(0, MAX_EVENTS - guaranteed.size);
  for (const e of events) {
    if (guaranteed.has(e.id)) trimmed.push(e);
    else if (fillBudget > 0) {
      trimmed.push(e);
      fillBudget -= 1;
    }
  }

  const counts = countByKind(trimmed);

  const payload: ActivityPayload = {
    events: trimmed,
    counts,
    schedulerRunning,
    pendingCount,
    activeLockCount,
    decisionsTruncated,
    sources,
    asOf: new Date().toISOString(),
  };
  return NextResponse.json(payload, {
    headers: { "Cache-Control": "no-store" },
  });
}

function orderTone(status: string): ActivityTone {
  const s = status.toUpperCase();
  if (s === "FILLED") return "bull";
  if (s === "REJECTED" || s === "CANCELED" || s === "EXPIRED") return "fox";
  if (s === "PARTIALLY_FILLED") return "gold";
  return "cyan";
}

function countByKind(events: ActivityEvent[]): Record<ActivityKind, number> {
  const c: Record<ActivityKind, number> = {
    scheduler: 0,
    permission: 0,
    decision: 0,
    risk: 0,
    order: 0,
    backtest: 0,
    runner: 0,
    conversation: 0,
  };
  for (const e of events) c[e.kind] += 1;
  return c;
}
