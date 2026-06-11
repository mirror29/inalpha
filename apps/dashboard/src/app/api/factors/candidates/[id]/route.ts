import { NextRequest, NextResponse } from "next/server";

import { backendFetch } from "@/lib/backend";
import type { FactorCandidate } from "@/lib/types";

export const dynamic = "force-dynamic";

// candidate id 是后端 uuid。校验格式后再内插路径(同 /api/lab/[id] 的防绕过约定)。
const UUID_RE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/**
 * POST /api/factors/candidates/[id] —— 人工审核(register / reject)。
 *
 * 这是 register 门的唯一入口(ADR-0019):factor 服务的 review 端点不挂任何
 * LLM tool,只有这里(人在 dashboard 点按钮)能把候选转正。
 */
export async function POST(
  req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = await params;
  if (!UUID_RE.test(id)) {
    return NextResponse.json({ ok: false, error: "invalid id" }, { status: 400 });
  }
  const body = (await req.json()) as {
    action: "register" | "reject";
    note?: string;
  };
  try {
    const updated = await backendFetch<FactorCandidate>(
      "factor",
      `/candidates/${encodeURIComponent(id)}/review`,
      {
        auth: false,
        method: "POST",
        body: {
          action: body.action,
          reviewed_by: "console:dev",
          note: body.note ?? null,
        },
        timeoutMs: 8000,
      },
    );
    return NextResponse.json({ ok: true, candidate: updated });
  } catch (e) {
    return NextResponse.json(
      { ok: false, error: e instanceof Error ? e.message : String(e) },
      { status: 502 },
    );
  }
}
