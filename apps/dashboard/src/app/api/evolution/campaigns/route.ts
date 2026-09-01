import { NextResponse } from "next/server";

import { backendFetch, BackendError } from "@/lib/backend";
import { isEvolutionEnabled } from "@/lib/evolution-capability";
import type { EvolutionCampaign, EvolutionCampaignPayload } from "@/lib/types";

export const dynamic = "force-dynamic";

/** Return the owner-scoped event campaign projection without candidate source blobs. */
export async function GET() {
  if (!isEvolutionEnabled()) {
    return NextResponse.json({ error: "evolution service is not enabled" }, { status: 503 });
  }
  try {
    const response = await backendFetch<{ items: EvolutionCampaign[] }>(
      "evolver",
      "/api/v1/campaigns",
      { query: { limit: 50 }, timeoutMs: 5_000 },
    );
    const payload: EvolutionCampaignPayload = {
      campaigns: response.items,
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
