import { NextResponse } from "next/server";

import { backendFetch, BackendError } from "@/lib/backend";
import type {
  RunDetailPayload,
  StrategyCandidateSummary,
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
 * 直接查后端单条 `GET /strategy_runs/{id}`（不再拉全列表 `.find()`——否则超出 list
 * LIMIT 的历史 run 永远 404）。后端 404 → run=null 走"未找到"分支；decisions 单独拉，
 * 失败降级为空不阻塞详情。两个并行。
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
    const [run, decisionsRes] = await Promise.all([
      backendFetch<StrategyRunRecord>("paper", `/strategy_runs/${id}`).catch(
        (err) => {
          // 404 = 该 run 不存在 / 非本账户 → 归一成 null 走"未找到"分支；其它错误上抛
          if (err instanceof BackendError && err.status === 404) return null;
          throw err;
        },
      ),
      backendFetch<StrategyRunDecisionRecord[]>(
        "paper",
        `/strategy_runs/${id}/decisions`,
        { query: { limit: 200 } },
      ).catch(() => [] as StrategyRunDecisionRecord[]),
    ]);

    // run 所跑的策略候选(用 candidate_id 反查)—— best-effort,失败/缺失为 null,
    // 详情页退化为只显 candidate_id 短码,不阻塞主体。run 不存在则无需查。
    const candidate = run
      ? await backendFetch<StrategyCandidateSummary>(
          "paper",
          `/strategy_candidates/${run.candidate_id}`,
        ).catch(() => null)
      : null;

    const payload: RunDetailPayload = {
      run,
      decisions: decisionsRes,
      candidate,
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
