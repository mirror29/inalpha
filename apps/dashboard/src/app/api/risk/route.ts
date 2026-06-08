import { NextResponse } from "next/server";

import { backendFetch } from "@/lib/backend";
import type {
  RiskEvent,
  RiskLock,
  RiskPayload,
  RiskRule,
  StrategyRunDecisionRecord,
  StrategyRunRecord,
} from "@/lib/types";

export const dynamic = "force-dynamic";

/** 聚合上限,避免跨 run fan-out 失控(同 /api/activity 口径)。 */
const MAX_EVENTS = 60;
const HISTORY_LIMIT = 50;
const MAX_RUNS_FOR_DECISIONS = 8;
const DECISIONS_PER_RUN = 50;

interface RulesResp {
  enabled: boolean;
  starting_balance: number;
  rules: RiskRule[];
}
interface LocksResp {
  locks: RiskLock[];
}
/** /risk/locks/history 一行 —— 比 active 锁多带状态/解锁元数据。 */
interface RecentLock extends RiskLock {
  active: boolean;
  unlocked_at: string | null;
  unlocked_by: string | null;
  unlock_reason: string | null;
}
interface HistoryResp {
  locks: RecentLock[];
}

/** 从拒单 reason 里解析风控规则名:`...[CooldownRule]...` → "CooldownRule"。 */
function parseRule(reason: string | null): string {
  if (!reason) return "risk";
  const m = reason.match(/\[([^\]]+)\]/);
  return m ? m[1] : "risk";
}

/** 历史锁的展示状态:生效中 / 已过期 / 人工解锁。 */
function lockStatus(l: RecentLock, nowMs: number): RiskEvent["status"] {
  const untilMs = new Date(l.locked_until).getTime();
  if (l.active && untilMs > nowMs) return "active";
  // reconciler/expire 用 unlocked_by='system'+unlock_reason='expired';人工解锁是真实用户。
  if (
    !l.active &&
    l.unlocked_by &&
    l.unlocked_by !== "system" &&
    l.unlock_reason !== "expired"
  ) {
    return "unlocked";
  }
  return "expired";
}

/**
 * GET /api/risk —— 风控面板:规则配置 + 当前活跃锁 + **最近风控事件**。
 *
 * 活跃锁(/risk/locks)只是「当前生效」的实时视图,短时效锁(如 CooldownRule 5min)
 * 一过期就消失。events 把两类「事后可查」的风控痕迹归一进一个时间线:
 *  1. 历史锁(/risk/locks/history,含已过期/已解锁)
 *  2. 跨 run 被风控拦的下单(strategy_run_decisions.outcome='risk_rejected',可能没产生锁)
 * 每源独立 allSettled 降级:任一取不到只标 sources.<x>=false,绝不把"取不到"当"没有"。
 */
export async function GET() {
  const sources = { rules: true, locks: true, history: true, rejections: true };

  const [rulesR, locksR, historyR, runsR] = await Promise.allSettled([
    backendFetch<RulesResp>("paper", "/risk/rules"),
    backendFetch<LocksResp>("paper", "/risk/locks"),
    backendFetch<HistoryResp>("paper", "/risk/locks/history", {
      query: { limit: HISTORY_LIMIT },
    }),
    // 只为"跨 run 决策"取最近 N 条 —— 上限推给服务端(按 started_at DESC),
    // 不随 run 历史累积拉全量大载荷。
    backendFetch<StrategyRunRecord[]>("paper", "/strategy_runs", {
      query: { limit: MAX_RUNS_FOR_DECISIONS },
    }),
  ]);

  const rules = rulesR.status === "fulfilled" ? rulesR.value : null;
  if (rulesR.status !== "fulfilled") sources.rules = false;
  const locks = locksR.status === "fulfilled" ? locksR.value.locks : [];
  if (locksR.status !== "fulfilled") sources.locks = false;

  const nowMs = Date.now();
  const events: RiskEvent[] = [];

  // ── 历史锁(含已过期 / 已解锁)──
  if (historyR.status === "fulfilled") {
    for (const l of historyR.value.locks) {
      events.push({
        id: `lock:${l.id}`,
        kind: "lock",
        ts: l.locked_at,
        rule: l.rule_name,
        scope: l.scope,
        label: [l.market, l.symbol].filter(Boolean).join(" ") || l.scope,
        reason: l.reason,
        status: lockStatus(l, nowMs),
        until: l.locked_until,
        href: null,
      });
    }
  } else {
    sources.history = false;
  }

  // ── 跨 run 被风控拒的下单(bounded fan-out,同 /api/activity)──
  if (runsR.status === "fulfilled") {
    const recentRuns = [...runsR.value]
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
        if (d.outcome !== "risk_rejected") continue;
        events.push({
          id: `rej:${d.id}`,
          kind: "rejection",
          ts: d.bar_ts,
          rule: parseRule(d.reason),
          scope: "symbol",
          label: run.symbol,
          reason: d.reason ?? `${d.side} ${d.quantity}`,
          status: "rejected",
          until: null,
          href: `/runners/${run.id}`,
        });
      }
    }
  } else {
    sources.rejections = false;
  }

  events.sort((a, b) => +new Date(b.ts) - +new Date(a.ts));
  const trimmed = events.slice(0, MAX_EVENTS);

  const payload: RiskPayload = {
    enabled: rules?.enabled ?? false,
    starting_balance: rules?.starting_balance ?? 0,
    rules: rules?.rules ?? [],
    locks,
    events: trimmed,
    sources,
    asOf: new Date().toISOString(),
  };
  return NextResponse.json(payload, {
    headers: { "Cache-Control": "no-store" },
  });
}
