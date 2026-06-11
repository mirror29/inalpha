import { NextRequest, NextResponse } from "next/server";

import { backendFetch } from "@/lib/backend";
import type { FactorCandidate } from "@/lib/types";

export const dynamic = "force-dynamic";

/**
 * GET /api/factors/candidates[?status=] —— 因子候选池列表(审核区块用)。
 *
 * factor 服务无 DB 时上游 503 → 这里透传 available=false,前端显示"候选池不可用"
 * 而非整页报错。
 */
export async function GET(req: NextRequest) {
  const status = req.nextUrl.searchParams.get("status");
  const qs = status ? `?status=${encodeURIComponent(status)}` : "";
  try {
    const candidates = await backendFetch<FactorCandidate[]>(
      "factor",
      `/candidates${qs}`,
      { auth: false, timeoutMs: 6000 },
    );
    return NextResponse.json({ available: true, candidates });
  } catch {
    return NextResponse.json({ available: false, candidates: [] });
  }
}
