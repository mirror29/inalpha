import { NextResponse } from "next/server";

import { backendFetch } from "@/lib/backend";
import { listChatThreads } from "@/lib/mastra";
import type {
  ActivityEvent,
  ActivityKind,
  ActivityPayload,
  ActivityTone,
  OrderRecord,
  StrategyRunDecisionRecord,
  StrategyRunRecord,
} from "@/lib/types";

export const dynamic = "force-dynamic";

/** 聚合时的上限,避免跨服务 fan-out 失控。 */
const MAX_EVENTS = 120;
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
 * 把 scheduler runs / 待审批 / 风控锁 / runner 决策 / 订单 归一成 ActivityEvent[],
 * 按时间倒序合并。每个源独立 try —— 任一不可用(尤其 mastra:4111 可能没起)只标记
 * sources.<x>=false,不拖垮整页;绝不把"取不到"静默当成"没有"。
 */
export async function GET() {
  const sources = {
    scheduler: true,
    permissions: true,
    risk: true,
    runs: true,
    orders: true,
    conversations: true,
  };
  const events: ActivityEvent[] = [];
  let schedulerRunning = false;
  let pendingCount = 0;
  let activeLockCount = 0;

  // mastra(scheduler / permissions):dev 端不需要 JWT,auth:false。
  const [jobsR, runsR, pendingR, locksR, strategyRunsR, ordersR, threadsR] =
    await Promise.allSettled([
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
      // 用户对话(mastra memory threads)—— 让"发起了什么会话"进入活动流。
      // 8s 轮询热路径:跳过标题回填(下方用 `#id` 兜底),回填只在历史下拉里做。
      listChatThreads(40, { backfillTitles: false }),
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

  // ── 订单流 ──
  if (ordersR.status === "fulfilled") {
    for (const o of ordersR.value) {
      events.push({
        id: `ord:${o.client_order_id}`,
        kind: "order",
        ts: o.ts_event,
        title: `${o.symbol ?? "—"} · ${o.side}`,
        detail: `${o.type} · ${o.status}`,
        outcome: o.status,
        tone: orderTone(o.status),
        href: null,
      });
    }
  } else {
    sources.orders = false;
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

  // 按时间倒序合并 + 截断。
  events.sort((a, b) => +new Date(b.ts) - +new Date(a.ts));
  const trimmed = events.slice(0, MAX_EVENTS);

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
    conversation: 0,
  };
  for (const e of events) c[e.kind] += 1;
  return c;
}
