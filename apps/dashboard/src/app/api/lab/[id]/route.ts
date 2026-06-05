import { NextResponse } from "next/server";

import { backendFetch, BackendError } from "@/lib/backend";
import type { CandidateDetailPayload, StrategyCandidateRecord } from "@/lib/types";

export const dynamic = "force-dynamic";

// candidate id 是后端 uuid4。校验格式后再内插路径,挡 `..` / 编码绕过导致
// new URL(path, base) 归一到后端根路径、把非预期 endpoint 响应当候选详情返回。
const UUID_RE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/**
 * GET /api/lab/[id] —— 单个候选详情(含源码 + 审计)。
 * 后端 404 → 透传 404(前端显示"未找到")。
 */
export async function GET(
  _req: Request,
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = await params;
  if (!UUID_RE.test(id)) {
    return NextResponse.json({ error: "invalid candidate id" }, { status: 400 });
  }
  try {
    const candidate = await backendFetch<StrategyCandidateRecord>(
      "paper",
      `/strategy_candidates/${id}`,
    );
    const payload: CandidateDetailPayload = {
      candidate,
      asOf: new Date().toISOString(),
    };
    return NextResponse.json(payload, {
      headers: { "Cache-Control": "no-store" },
    });
  } catch (err) {
    if (err instanceof BackendError) {
      // 404 也归一成 {candidate:null} 让前端走"未找到"分支,而非整页错误。
      if (err.status === 404) {
        const payload: CandidateDetailPayload = {
          candidate: null,
          asOf: new Date().toISOString(),
        };
        return NextResponse.json(payload, { status: 404 });
      }
      return NextResponse.json(
        { error: err.message, detail: err.detail },
        { status: err.status },
      );
    }
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "unknown error" },
      { status: 500 },
    );
  }
}
