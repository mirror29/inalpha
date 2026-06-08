import { NextResponse } from "next/server";

import { backendFetch, BackendError } from "@/lib/backend";
import type { LabPayload, StrategyCandidateSummary } from "@/lib/types";

export const dynamic = "force-dynamic";

/**
 * GET /api/lab —— 策略候选列表(后端已按 fitness DESC 排序)。
 * 透传 /strategy_candidates,顺带按 status 计数给过滤器角标用。
 */
export async function GET() {
  try {
    // 多取 1 条探测「是否还有更多候选」(命中上限 → 截断提示,不静默)。
    const CANDIDATES_SHOWN = 100;
    const raw = await backendFetch<StrategyCandidateSummary[]>(
      "paper",
      "/strategy_candidates",
      { query: { limit: CANDIDATES_SHOWN + 1 } },
    );
    const truncated = raw.length > CANDIDATES_SHOWN;
    const candidates = raw.slice(0, CANDIDATES_SHOWN);
    const counts = {
      all: candidates.length,
      promoted: candidates.filter((c) => c.status === "promoted").length,
      candidate: candidates.filter((c) => c.status === "candidate").length,
      rejected: candidates.filter((c) => c.status === "rejected").length,
    };
    const payload: LabPayload = {
      candidates,
      counts,
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
