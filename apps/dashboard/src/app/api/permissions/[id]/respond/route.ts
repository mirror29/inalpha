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
      { method: "POST", body: { decision }, timeoutMs: 70_000 },
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
    const result = await backendFetch<{ status: string; deadline?: string }>(
      "mastra", `/permissions/${encodeURIComponent(requestId)}/status`, { timeoutMs: 5_000 },
    );
    if (result.status === "approved" || result.status === "unavailable") {
      return NextResponse.json({ status: result.status }, { headers: { "Cache-Control": "no-store" } });
    }
    const deadline = result.deadline ? Date.parse(result.deadline) : Number.NaN;
    if (result.status !== "pending" || !Number.isFinite(deadline)) throw new Error("invalid approval status");
    return NextResponse.json({ status: "pending", deadline: result.deadline, remainingMs: Math.max(0, deadline - Date.now()) },
      { headers: { "Cache-Control": "no-store" } });
  } catch {
    return NextResponse.json({ error: "approval_status_unavailable" }, { status: 503 });
  }
}
