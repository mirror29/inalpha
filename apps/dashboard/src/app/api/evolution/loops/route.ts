import { NextResponse } from "next/server";

import { backendFetch, BackendError } from "@/lib/backend";
import type { EvolutionLoop, EvolutionLoopPayload } from "@/lib/types";

export const dynamic = "force-dynamic";

/** Return the compact owner-scoped workflow projection used by the evolution workspace. */
export async function GET() {
  try {
    const response = await backendFetch<{ items: EvolutionLoop[] }>(
      "evolver",
      "/api/v1/evolution-loops",
      { query: { limit: 50 }, timeoutMs: 5_000 },
    );
    const payload: EvolutionLoopPayload = {
      loops: response.items,
      asOf: new Date().toISOString(),
    };
    return NextResponse.json(payload, { headers: { "Cache-Control": "no-store" } });
  } catch (error) {
    return NextResponse.json(
      { error: error instanceof Error ? error.message : "unknown error" },
      { status: error instanceof BackendError ? error.status : 500 },
    );
  }
}
