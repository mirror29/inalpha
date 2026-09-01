import { NextResponse } from "next/server";

import { backendFetch, BackendError } from "@/lib/backend";
import type { EventDataCoverage } from "@/lib/types";

export const dynamic = "force-dynamic";

/** Proxy event-ledger health while keeping service JWTs on the BFF. */
export async function GET() {
  try {
    const coverage = await backendFetch<EventDataCoverage>("data", "/events/coverage", {
      timeoutMs: 5_000,
    });
    return NextResponse.json(coverage, { headers: { "Cache-Control": "no-store" } });
  } catch (error) {
    return NextResponse.json(
      { error: error instanceof Error ? error.message : "unknown error" },
      { status: error instanceof BackendError ? error.status : 500 },
    );
  }
}
