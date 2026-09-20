import { NextResponse } from "next/server";

import { backendFetch, BackendError } from "@/lib/backend";
import type { EvolutionLoop, EvolutionLoopDetailPayload } from "@/lib/types";

export const dynamic = "force-dynamic";

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

/** Load one durable loop projection without eagerly loading candidates or source code. */
export async function GET(
  _request: Request,
  { params }: { params: Promise<{ loopId: string }> },
) {
  const { loopId } = await params;
  if (!UUID_RE.test(loopId)) {
    return NextResponse.json({ error: "invalid evolution loop id" }, { status: 400 });
  }
  try {
    const loop = await backendFetch<EvolutionLoop>(
      "evolver",
      `/api/v1/evolution-loops/${loopId}`,
      { timeoutMs: 5_000 },
    );
    const payload: EvolutionLoopDetailPayload = { loop, asOf: new Date().toISOString() };
    return NextResponse.json(payload, { headers: { "Cache-Control": "no-store" } });
  } catch (error) {
    return NextResponse.json(
      { error: error instanceof Error ? error.message : "unknown error" },
      { status: error instanceof BackendError ? error.status : 500 },
    );
  }
}
