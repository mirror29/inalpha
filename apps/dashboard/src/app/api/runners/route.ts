import { NextResponse } from "next/server";

import { backendFetch, BackendError } from "@/lib/backend";
import type { RunnersPayload, StrategyRunRecord } from "@/lib/types";

export const dynamic = "force-dynamic";

/**
 * GET /api/runners —— Live Runner 列表。
 * 直接透传 paper /strategy_runs(当前账户的全部 run),并算运行中计数。
 */
export async function GET() {
  try {
    // 多取 1 条探测「是否还有更早的 run」(命中上限 → 截断提示,不静默)。
    const RUNS_SHOWN = 200;
    const raw = await backendFetch<StrategyRunRecord[]>(
      "paper",
      "/strategy_runs",
      { query: { limit: RUNS_SHOWN + 1 } },
    );
    const truncated = raw.length > RUNS_SHOWN;
    const runs = raw.slice(0, RUNS_SHOWN);
    const payload: RunnersPayload = {
      runs,
      runningCount: runs.filter((r) => r.status === "running").length,
      truncated,
      asOf: new Date().toISOString(),
    };
    return NextResponse.json(payload, {
      headers: { "Cache-Control": "no-store" },
    });
  } catch (err) {
    if (err instanceof BackendError) {
      return NextResponse.json(
        { error: err.message, detail: err.detail },
        { status: err.status },
      );
    }
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "unknown error" },
      { status: 500 },
    );
  }
}
