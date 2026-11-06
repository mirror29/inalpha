import { NextResponse } from "next/server";
import { backendFetch, BackendError } from "@/lib/backend";

export const dynamic = "force-dynamic";

/** Use the authenticated orchestration preparation API without invoking chat or forwarding model keys. */
export async function POST(request: Request) {
  try {
    const body = await request.json();
    const result = await backendFetch("mastra", "/evolution/prepare", {
      method: "POST", body, timeoutMs: 120_000,
    });
    return NextResponse.json(result, { headers: { "Cache-Control": "no-store" } });
  } catch (error) {
    return NextResponse.json({ error: "EXPERIMENT_PREPARATION_UNAVAILABLE" }, {
      status: error instanceof BackendError ? error.status : 400,
    });
  }
}
