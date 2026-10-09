import { NextRequest, NextResponse } from "next/server";

import { backendFetch } from "@/lib/backend";

/** Proxies one owner-authenticated approval decision to the private Mastra API. */
export async function POST(
  request: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const requestId = (await params).id;
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: "bad_request" }, { status: 400 });
  }
  const decision = (body as { decision?: unknown } | null)?.decision;
  if (decision !== "allow" && decision !== "deny") {
    return NextResponse.json({ error: "bad_request" }, { status: 400 });
  }
  try {
    const result = await backendFetch<unknown>(
      "mastra",
      `/permissions/${encodeURIComponent(requestId)}/respond`,
      { method: "POST", body: { decision }, timeoutMs: 5_000 },
    );
    return NextResponse.json(result);
  } catch (error) {
    const candidate = (error as { status?: unknown } | null)?.status;
    const status = typeof candidate === "number" ? candidate : 502;
    return NextResponse.json({ error: "approval_failed" }, { status });
  }
}

/** Check live owner-scoped availability before rendering historical approval controls. */
export async function GET(
  _request: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const requestId = (await params).id;
  try {
    const result = await backendFetch<{ pending: { requestId: string; deadline: string }[] }>(
      "mastra", "/permissions/pending", { timeoutMs: 5_000 },
    );
    const pending = result.pending.find((item) => item.requestId === requestId);
    const deadline = pending ? Date.parse(pending.deadline) : undefined;
    if (pending && !Number.isFinite(deadline)) throw new Error("invalid approval deadline");
    return NextResponse.json(pending
      ? { status: "pending", deadline: pending.deadline, remainingMs: Math.max(0, deadline! - Date.now()) }
      : { status: "unavailable" }, { headers: { "Cache-Control": "no-store" } });
  } catch {
    return NextResponse.json({ error: "approval_status_unavailable" }, { status: 503 });
  }
}
