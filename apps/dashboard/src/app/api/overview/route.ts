import { NextResponse } from "next/server";

import { backendFetch, BackendError } from "@/lib/backend";
import type {
  AccountSnapshot,
  OrderRecord,
  OverviewPayload,
  PositionRecord,
  PositionWithMark,
  StrategyCandidateSummary,
  StrategyRunRecord,
  TickerResponse,
} from "@/lib/types";

/**
 * 总览策略池面板展示的候选条数上限(后端已按 fitness DESC 排,取头部即可)。
 * 与最近订单 ORDERS_SHOWN 对齐:两卡在总览同排 grid 里 max-h-96 内滚,条数相当才不留白。
 */
const CANDIDATES_SHOWN = 20;

export const dynamic = "force-dynamic";

/**
 * GET /api/overview —— 组合总览的聚合负载。
 *
 * server 侧并行 fan-out paper service 的四个接口,再 best-effort 给每个持仓补最新价
 * (data /ticker)算浮动盈亏。account 拿不到 = 整页失败;positions/orders/runs 单个失败
 * 降级成空(不让一个慢接口拖垮整页);ticker 失败 = 该行 mark 标 stale,不阻塞。
 *
 * 不在浏览器直连后端:python service 没配 CORS,且 dev token 留在 server 侧。
 */
export async function GET() {
  let account: AccountSnapshot;
  try {
    account = await backendFetch<AccountSnapshot>("paper", "/accounts/me");
  } catch (err) {
    return errorResponse(err);
  }

  // positions / orders / runs —— 任一失败降级为空,不整页挂。
  // orders 多取 1 条探测「是否还有更早的」(命中上限 → 截断提示,不静默)。
  const ORDERS_SHOWN = 20;
  const [positionsRes, ordersRes, runsRes, candidatesRes] =
    await Promise.allSettled([
      backendFetch<PositionRecord[]>("paper", "/positions"),
      backendFetch<OrderRecord[]>("paper", "/orders", {
        query: { limit: ORDERS_SHOWN + 1 },
      }),
      // 显式取后端硬上限:默认 limit(runs=200/candidates=50)会让 KPI 计数在
      // 数量超限后系统性偏小(E2 演化积累候选很快触发),且无任何提示。
      backendFetch<StrategyRunRecord[]>("paper", "/strategy_runs", {
        query: { limit: 1000 },
      }),
      backendFetch<StrategyCandidateSummary[]>("paper", "/strategy_candidates", {
        query: { limit: 200 },
      }),
    ]);

  const positions = settledOr(positionsRes, []);
  const ordersRaw = settledOr(ordersRes, []);
  const ordersTruncated = ordersRaw.length > ORDERS_SHOWN;
  const orders = ordersRaw.slice(0, ORDERS_SHOWN);
  const runs = settledOr(runsRes, []);

  // 策略池:计数取自完整集合(promoted/候选),面板只展示头部 N 条。
  const candidatesAll = settledOr(candidatesRes, []);
  const candidateCounts = {
    all: candidatesAll.length,
    promoted: candidatesAll.filter((c) => c.status === "promoted").length,
    candidate: candidatesAll.filter((c) => c.status === "candidate").length,
  };
  const candidates = candidatesAll.slice(0, CANDIDATES_SHOWN);

  // 每个持仓 best-effort 补最新价(fresh=false:只读 DB 缓存,不触发慢 backfill)。
  const marked: PositionWithMark[] = await Promise.all(
    positions.map(async (p): Promise<PositionWithMark> => {
      try {
        const ticker = await backendFetch<TickerResponse>("data", "/ticker", {
          query: { venue: p.venue, symbol: p.symbol, fresh: false },
          timeoutMs: 4000,
        });
        const unrealized = (ticker.price - p.avg_open_price) * p.quantity;
        return {
          ...p,
          mark_price: ticker.price,
          mark_stale: ticker.is_stale,
          unrealized_pnl: unrealized,
        };
      } catch {
        // 最新价拿不到 —— 不猜价,标 stale,浮动盈亏留空(金融时效硬约束)。
        return { ...p, mark_price: null, mark_stale: true, unrealized_pnl: null };
      }
    }),
  );

  const payload: OverviewPayload = {
    account,
    positions: marked,
    orders,
    runs,
    activeRunnerCount: runs.filter((r) => r.status === "running").length,
    candidates,
    candidateCounts,
    ordersTruncated,
    asOf: new Date().toISOString(),
  };

  return NextResponse.json(payload, {
    headers: { "Cache-Control": "no-store" },
  });
}

function settledOr<T>(res: PromiseSettledResult<T>, fallback: T): T {
  return res.status === "fulfilled" ? res.value : fallback;
}

function errorResponse(err: unknown) {
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
