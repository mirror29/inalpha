import { NextResponse } from "next/server";

import { evolutionBackendFetch } from "@/lib/evolver-backend";
import type { EvolutionRunDetailPayload } from "@/lib/types";

export const dynamic = "force-dynamic";

/**
 * GET /api/evolution/[runId] —— 演化运行详情（含候选完整列表）。
 *
 * 调 evolver 服务的 GET /api/v1/runs/{run_id}。
 */
export async function GET(
  _request: Request,
  { params }: { params: Promise<{ runId: string }> },
) {
  const { runId } = await params;
  try {
    const data = await evolutionBackendFetch(`/api/v1/runs/${runId}`, {
      timeoutMs: 5000,
    });
    const payload: EvolutionRunDetailPayload = {
      run: data as EvolutionRunDetailPayload["run"],
      asOf: new Date().toISOString(),
    };
    return NextResponse.json(payload, {
      headers: { "Cache-Control": "no-store" },
    });
  } catch (err) {
    console.error("[evolution:run] fetch failed", err);
    return NextResponse.json({ error: err instanceof Error ? err.message : "unknown error" }, { status: 500 });
  }
}