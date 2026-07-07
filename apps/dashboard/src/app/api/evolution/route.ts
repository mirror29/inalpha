import { NextResponse } from "next/server";

import { evolutionBackendFetch } from "@/lib/evolver-backend";
import type { EvolutionPayload, EvolutionRunSummary } from "@/lib/types";

export const dynamic = "force-dynamic";

/**
 * GET /api/evolution —— 演化运行列表（按 started_at 倒序）。
 *
 * 调 evolver 服务的 GET /api/v1/runs（目前内存存储，evolver 重启后清空）。
 * 每个 run 附带候选摘要（按 fitness 降序，取前 3 条）。
 */
export async function GET() {
  try {
    const RUNS_LIMIT = 50;
    const raw = await evolutionBackendFetch<EvolutionRunSummary[]>(
      "/api/v1/runs",
      { query: { limit: RUNS_LIMIT + 1 }, timeoutMs: 5000 },
    );
    const truncated = raw.length > RUNS_LIMIT;
    const runs = raw.slice(0, RUNS_LIMIT);

    const payload: EvolutionPayload = {
      runs,
      truncated,
      asOf: new Date().toISOString(),
    };
    return NextResponse.json(payload, {
      headers: { "Cache-Control": "no-store" },
    });
  } catch (err) {
    console.error("[evolution] fetch failed", err);
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "unknown error" },
      { status: 500 },
    );
  }
}