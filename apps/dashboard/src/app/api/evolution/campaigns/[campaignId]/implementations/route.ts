import { NextResponse } from "next/server";
import { backendFetch, BackendError } from "@/lib/backend";
import type { EvolutionImplementationPage } from "@/lib/types";

export const dynamic = "force-dynamic";
const UUID =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

/** Page research evidence through the authenticated owner's existing Evolver read API. */
export async function GET(
  request: Request,
  { params }: { params: Promise<{ campaignId: string }> },
) {
  const { campaignId } = await params;
  const query = new URL(request.url).searchParams;
  const limit = Number(query.get("limit") ?? 24);
  const offset = Number(query.get("offset") ?? 0);
  const generation = query.has("generation")
    ? Number(query.get("generation"))
    : undefined;
  if (
    !UUID.test(campaignId) ||
    !Number.isSafeInteger(limit) ||
    limit < 1 ||
    limit > 100 ||
    !Number.isSafeInteger(offset) ||
    offset < 0 ||
    (generation !== undefined &&
      (!Number.isSafeInteger(generation) || generation < 1 || generation > 5))
  ) {
    return NextResponse.json(
      { error: "invalid campaign pagination" },
      { status: 400 },
    );
  }
  try {
    const page = await backendFetch<EvolutionImplementationPage>(
      "evolver",
      `/api/v1/campaigns/${campaignId}/implementations`,
      {
        query: {
          limit,
          offset,
          ...(generation !== undefined ? { generation } : {}),
        },
        timeoutMs: 5000,
      },
    );
    return NextResponse.json(page, {
      headers: { "Cache-Control": "no-store" },
    });
  } catch (error) {
    return NextResponse.json(
      { error: error instanceof Error ? error.message : "unknown error" },
      { status: error instanceof BackendError ? error.status : 500 },
    );
  }
}
