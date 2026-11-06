import { withLineageOrigin } from "@/lib/evolution-display-target";
import { NextResponse } from "next/server";

import { backendFetch, BackendError } from "@/lib/backend";
import { isEvolutionEnabled } from "@/lib/evolution-capability";
import type { EvolutionRun, EvolutionRunDetailPayload } from "@/lib/types";

export const dynamic = "force-dynamic";

function validUuid(value: string): boolean {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value);
}

function failure(error: unknown) {
  const status = error instanceof BackendError ? error.status : 500;
  return NextResponse.json(
    { error: error instanceof Error ? error.message : "unknown error" },
    { status },
  );
}

function disabled() {
  return NextResponse.json(
    { error: "evolution service is not enabled", code: "EVOLUTION_SERVICE_DISABLED" },
    { status: 503 },
  );
}

export async function GET(
  _request: Request,
  { params }: { params: Promise<{ runId: string }> },
) {
  if (!isEvolutionEnabled()) return disabled();
  const { runId } = await params;
  if (!validUuid(runId)) return NextResponse.json({ error: "invalid run id" }, { status: 400 });
  try {
    const run = await backendFetch<EvolutionRun>("evolver", `/api/v1/runs/${runId}`, {
      timeoutMs: 5000,
    });
    run.lineage = await withLineageOrigin(run.lineage);
    const payload: EvolutionRunDetailPayload = { run, asOf: new Date().toISOString() };
    return NextResponse.json(payload, { headers: { "Cache-Control": "no-store" } });
  } catch (error) {
    return failure(error);
  }
}

export async function POST(
  _request: Request,
  { params }: { params: Promise<{ runId: string }> },
) {
  if (!isEvolutionEnabled()) return disabled();
  const { runId } = await params;
  if (!validUuid(runId)) return NextResponse.json({ error: "invalid run id" }, { status: 400 });
  try {
    const run = await backendFetch<EvolutionRun>("evolver", `/api/v1/runs/${runId}/abort`, {
      method: "POST",
      body: {},
      timeoutMs: 5000,
    });
    return NextResponse.json({ run, asOf: new Date().toISOString() });
  } catch (error) {
    return failure(error);
  }
}
