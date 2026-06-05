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
    const runs = await backendFetch<StrategyRunRecord[]>(
      "paper",
      "/strategy_runs",
      { query: { limit: 200 } },
    );
    const payload: RunnersPayload = {
      runs,
      runningCount: runs.filter((r) => r.status === "running").length,
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
