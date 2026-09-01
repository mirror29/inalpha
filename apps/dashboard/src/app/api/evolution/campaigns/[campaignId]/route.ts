import { NextResponse } from "next/server";

import { backendFetch, BackendError } from "@/lib/backend";
import { isEvolutionEnabled } from "@/lib/evolution-capability";
import type { EvolutionCampaign, EvolutionCampaignDetailPayload } from "@/lib/types";

export const dynamic = "force-dynamic";

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

/** Load a generation-level campaign read model; evidence curves stay lazy on the backend. */
export async function GET(
  _request: Request,
  { params }: { params: Promise<{ campaignId: string }> },
) {
  if (!isEvolutionEnabled()) return NextResponse.json({ error: "disabled" }, { status: 503 });
  const { campaignId } = await params;
  if (!UUID_RE.test(campaignId)) {
    return NextResponse.json({ error: "invalid campaign id" }, { status: 400 });
  }
  try {
    const campaign = await backendFetch<EvolutionCampaign>(
      "evolver",
      `/api/v1/campaigns/${campaignId}`,
      { timeoutMs: 5_000 },
    );
    const payload: EvolutionCampaignDetailPayload = { campaign, asOf: new Date().toISOString() };
    return NextResponse.json(payload, { headers: { "Cache-Control": "no-store" } });
  } catch (error) {
    return NextResponse.json(
      { error: error instanceof Error ? error.message : "unknown error" },
      { status: error instanceof BackendError ? error.status : 500 },
    );
  }
}

/** The only operator action is experimental adoption; it never promotes or starts Runner. */
export async function POST(
  _request: Request,
  { params }: { params: Promise<{ campaignId: string }> },
) {
  if (!isEvolutionEnabled()) return NextResponse.json({ error: "disabled" }, { status: 503 });
  const { campaignId } = await params;
  if (!UUID_RE.test(campaignId)) {
    return NextResponse.json({ error: "invalid campaign id" }, { status: 400 });
  }
  try {
    const adoption = await backendFetch<Record<string, unknown>>(
      "evolver",
      `/api/v1/campaigns/${campaignId}/adopt`,
      { method: "POST", body: {}, timeoutMs: 5_000 },
    );
    return NextResponse.json({ adoption });
  } catch (error) {
    return NextResponse.json(
      { error: error instanceof Error ? error.message : "unknown error" },
      { status: error instanceof BackendError ? error.status : 500 },
    );
  }
}
