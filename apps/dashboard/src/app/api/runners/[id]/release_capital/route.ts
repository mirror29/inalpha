import { NextResponse } from "next/server";
import { backendFetch, BackendError } from "@/lib/backend";

/** Release capital through the current user's authenticated backend session. */
export async function POST(req: Request, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(id)) {
    return NextResponse.json({ error: "invalid run id" }, { status: 400 });
  }
  const origin = req.headers.get("origin");
  if (origin && origin !== new URL(req.url).origin) {
    return NextResponse.json({ error: "invalid origin" }, { status: 403 });
  }
  try {
    return NextResponse.json(await backendFetch("paper", `/strategy_runs/${id}/release_capital`, { method: "POST" }));
  } catch (err) {
    return NextResponse.json({ error: err instanceof Error ? err.message : "unknown error" }, { status: err instanceof BackendError ? err.status : 500 });
  }
}
