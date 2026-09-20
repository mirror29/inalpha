import { NextResponse } from "next/server";

import { BackendError } from "@/lib/backend";
import { getEventEvolutionCapability } from "@/lib/evolution-capability";

export const dynamic = "force-dynamic";

/** Proxy the Evolver-owned E2 gate without inventing a Dashboard-side feature flag. */
export async function GET() {
  try {
    const capability = await getEventEvolutionCapability();
    return NextResponse.json(capability, { headers: { "Cache-Control": "no-store" } });
  } catch (error) {
    return NextResponse.json(
      { error: error instanceof Error ? error.message : "unknown error" },
      { status: error instanceof BackendError ? error.status : 500 },
    );
  }
}
