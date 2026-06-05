import { NextResponse } from "next/server";

import { backendFetch, BackendError } from "@/lib/backend";
import type {
  RunDetailPayload,
  StrategyRunDecisionRecord,
  StrategyRunRecord,
} from "@/lib/types";

export const dynamic = "force-dynamic";

// run id 是后端 uuid4。校验格式后再内插路径,挡 `..` / 编码绕过导致 new URL 归一到
// 后端根路径(backendFetch 用 new URL(path, base) 会 normalize 路径段)。
const UUID_RE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/**
 * GET /api/runners/[id] —— 单个 run 详情 + 决策时间线。
 *
 * 后端没有「单个 run」GET,所以从 /strategy_runs 列表里按 id 找;decisions 单独拉
 * (默认 200 条,后端上限 500)。两个并行,decisions 失败降级为空不阻塞详情。
 */
export async function GET(
  _req: Request,
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = await params;
  if (!UUID_RE.test(id)) {
    return NextResponse.json({ error: "invalid run id" }, { status: 400 });
  }

  try {
    const [runs, decisionsRes] = await Promise.all([
      backendFetch<StrategyRunRecord[]>("paper", "/strategy_runs", {
        query: { limit: 200 },
      }),
      backendFetch<StrategyRunDecisionRecord[]>(
        "paper",
        `/strategy_runs/${id}/decisions`,
        { query: { limit: 200 } },
      ).catch(() => [] as StrategyRunDecisionRecord[]),
    ]);

    const run = runs.find((r) => r.id === id) ?? null;

    const payload: RunDetailPayload = {
      run,
      decisions: decisionsRes,
      asOf: new Date().toISOString(),
    };
    return NextResponse.json(payload, {
      status: run ? 200 : 404,
      headers: { "Cache-Control": "no-store" },
    });
  } catch (err) {
    if (err instanceof BackendError) {
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
