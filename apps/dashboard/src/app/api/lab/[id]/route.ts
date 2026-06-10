import { NextResponse } from "next/server";

import { backendFetch, BackendError } from "@/lib/backend";
import type {
  BacktestRunSummary,
  BacktestTradeRecord,
  CandidateDetailPayload,
  StrategyCandidateRecord,
  StrategyRunDecisionRecord,
  StrategyRunRecord,
} from "@/lib/types";

/** 后端 GET /backtest_runs 一行(只取本页要的字段)。 */
interface RawBacktestRun {
  run_id: string;
  config: Record<string, unknown>;
  metrics: Record<string, unknown> | null;
  created_at: string;
}

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

    // 该候选派生的 live runner —— 后端无「按 candidate 查 run」端点,拉列表本地过滤
    // (best-effort,失败降级空,不阻塞详情)。最近一个 run 再拉决策给 K 线叠加 + 历史交易。
    // limit 显式取后端硬上限 1000:默认 200 时老候选的 run 可能全被挤出窗口,
    // 详情页假装"没跑过"。全局 run 超 1000 后仍可能丢,根治等 ?candidate_id= 过滤端点。
    const allRuns = await backendFetch<StrategyRunRecord[]>(
      "paper",
      "/strategy_runs",
      { query: { limit: 1000 } },
    ).catch(() => [] as StrategyRunRecord[]);
    const runs = allRuns
      .filter((r) => r.candidate_id === id)
      .sort((a, b) => b.started_at.localeCompare(a.started_at));
    const latestRunDecisions = runs[0]
      ? await backendFetch<StrategyRunDecisionRecord[]>(
          "paper",
          `/strategy_runs/${runs[0].id}/decisions`,
          { query: { limit: 200 } },
        ).catch(() => [] as StrategyRunDecisionRecord[])
      : [];

    // 最近一次回测概要(回测时间/区间)+ 逐笔成交 —— best-effort,失败降级,不阻塞详情。
    // 回测 strategy_code 对候选路径固定为 "candidate:<id>"(见 paper runner)。
    // 取「最近一次**有成交**的回测,否则最近一次」:同一候选可能混有跑错标的/参数的
    // 0 成交脏 run(如 NVDA 策略灌 BTC 数据),只按 created_at 取最新会把脏 run 顶到
    // 详情页(K 线标的都不对)。展示哪个 run 在 BacktestMeta 标的/区间里是透明的。
    const backtestRunsRaw = await backendFetch<RawBacktestRun[]>("paper", "/backtest_runs", {
      query: { strategy_code: `candidate:${id}`, limit: 10 },
    }).catch(() => [] as RawBacktestRun[]);
    const latestBacktest =
      backtestRunsRaw.find((r) => Number(r.metrics?.num_trades) > 0) ??
      backtestRunsRaw[0];
    const backtestRun: BacktestRunSummary | null = latestBacktest
      ? {
          runId: latestBacktest.run_id,
          createdAt: latestBacktest.created_at,
          periodStart: strOrNull(latestBacktest.config.from_ts),
          periodEnd: strOrNull(latestBacktest.config.to_ts),
          venue: strOrNull(latestBacktest.config.venue),
          symbol: strOrNull(latestBacktest.config.symbol),
          timeframe: strOrNull(latestBacktest.config.timeframe),
          // 老 run 的 metrics 没有 initial_cash(新键),从 config 兜底补齐。
          metrics: withInitialCash(
            (latestBacktest.metrics ?? null) as Record<
              string,
              number | null
            > | null,
            latestBacktest.config,
          ),
        }
      : null;
    const backtestTrades = backtestRun
      ? await backendFetch<BacktestTradeRecord[]>(
          "paper",
          `/backtest_runs/${backtestRun.runId}/trades`,
          { query: { limit: 500 } },
        ).catch(() => [] as BacktestTradeRecord[])
      : [];

    const payload: CandidateDetailPayload = {
      candidate,
      runs,
      latestRunDecisions,
      backtestRun,
      backtestTrades,
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
          runs: [],
          latestRunDecisions: [],
          backtestRun: null,
          backtestTrades: [],
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

/** config 里的字段可能缺失/非字符串 —— 取字符串否则 null。 */
function strOrNull(v: unknown): string | null {
  return typeof v === "string" && v ? v : null;
}

/** metrics 缺 initial_cash(2026-06-10 前的老 run)时从 config.initial_cash 兜底。 */
function withInitialCash(
  metrics: Record<string, number | null> | null,
  config: Record<string, unknown>,
): Record<string, number | null> | null {
  if (metrics && typeof metrics.initial_cash === "number") return metrics;
  const fromConfig = config.initial_cash;
  if (typeof fromConfig !== "number") return metrics;
  return { ...(metrics ?? {}), initial_cash: fromConfig };
}
