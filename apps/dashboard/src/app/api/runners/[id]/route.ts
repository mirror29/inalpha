import { NextResponse } from "next/server";

import { backendFetch, BackendError } from "@/lib/backend";
import type {
  AccountSnapshot,
  PositionRecord,
  PositionWithMark,
  RunDetailPayload,
  StrategyCandidateSummary,
  StrategyRunDecisionRecord,
  StrategyRunRecord,
  TickerResponse,
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

    // run 所跑的策略候选(用 candidate_id 反查)+ 该标的账户持仓 + 计价货币 ——
    // 均 best-effort,失败/缺失为 null,不阻塞主体。run 不存在则无需查。
    const [candidate, position, baseCurrency] = run
      ? await Promise.all([
          backendFetch<StrategyCandidateSummary>(
            "paper",
            `/strategy_candidates/${run.candidate_id}`,
          ).catch(() => null),
          fetchPositionWithMark(run.venue, run.symbol),
          backendFetch<AccountSnapshot>("paper", "/accounts/me")
            .then((a) => a.base_currency)
            .catch(() => null),
        ])
      : [null, null, null];

    const payload: RunDetailPayload = {
      run,
      decisions: decisionsRes,
      candidate,
      position,
      baseCurrency,
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

/**
 * 该标的的账户当前持仓 + 最新价(浮动盈亏)。注意是**账户级**持仓 —— 同标的
 * 多个 run 共享同一仓位。空仓 / 接口失败返回 null;最新价拿不到不猜价,
 * mark 标 stale、浮动盈亏留空(金融时效硬约束,与总览同款)。
 */
async function fetchPositionWithMark(
  venue: string,
  symbol: string,
): Promise<PositionWithMark | null> {
  const positions = await backendFetch<PositionRecord[]>(
    "paper",
    "/positions",
  ).catch(() => [] as PositionRecord[]);
  const p = positions.find(
    (x) => x.venue === venue && x.symbol === symbol && x.quantity !== 0,
  );
  if (!p) return null;
  try {
    const ticker = await backendFetch<TickerResponse>("data", "/ticker", {
      query: { venue: p.venue, symbol: p.symbol, fresh: false },
      timeoutMs: 4000,
    });
    return {
      ...p,
      mark_price: ticker.price,
      mark_stale: ticker.is_stale,
      unrealized_pnl: (ticker.price - p.avg_open_price) * p.quantity,
    };
  } catch {
    return { ...p, mark_price: null, mark_stale: true, unrealized_pnl: null };
  }
}
