import { NextResponse } from "next/server";

import { backendFetch, BackendError } from "@/lib/backend";
import type {
  BacktestRunDetailPayload,
  BacktestTradeRecord,
} from "@/lib/types";

export const dynamic = "force-dynamic";

const UUID_RE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** 后端 GET /backtest_runs/{id} 一行(只取本页要的字段)。 */
interface RawBacktestRun {
  run_id: string;
  strategy_code: string;
  status: string;
  config: Record<string, unknown>;
  metrics: Record<string, unknown> | null;
  created_at: string;
}

/**
 * GET /api/backtests/[runId] —— 单次回测详情(概要 + 逐笔成交)。
 * 「Agent 活动」流点击回测事件的落地页;内置策略回测没有候选详情页,在这里复盘。
 */
export async function GET(
  _req: Request,
  { params }: { params: Promise<{ runId: string }> },
) {
  const { runId } = await params;
  if (!UUID_RE.test(runId)) {
    return NextResponse.json({ error: "invalid run id" }, { status: 400 });
  }
  try {
    const raw = await backendFetch<RawBacktestRun>(
      "paper",
      `/backtest_runs/${runId}`,
    );
    const candidateId =
      typeof raw.config.candidate_id === "string"
        ? raw.config.candidate_id
        : raw.strategy_code.startsWith("candidate:")
          ? raw.strategy_code.slice("candidate:".length)
          : null;
    // 候选回测补 description(标题可读);best-effort,失败退化为 strategy_code。
    let candidateDescription: string | null = null;
    if (candidateId) {
      candidateDescription = await backendFetch<{ description?: string }>(
        "paper",
        `/strategy_candidates/${candidateId}`,
      )
        .then((c) => c.description?.trim() || null)
        .catch(() => null);
    }
    const trades = await backendFetch<BacktestTradeRecord[]>(
      "paper",
      `/backtest_runs/${runId}/trades`,
      { query: { limit: 500 } },
    ).catch(() => [] as BacktestTradeRecord[]);

    const payload: BacktestRunDetailPayload = {
      run: {
        runId: raw.run_id,
        createdAt: raw.created_at,
        periodStart: strOrNull(raw.config.from_ts),
        periodEnd: strOrNull(raw.config.to_ts),
        venue: strOrNull(raw.config.venue),
        symbol: strOrNull(raw.config.symbol),
        timeframe: strOrNull(raw.config.timeframe),
        metrics: (raw.metrics ?? null) as Record<string, number | null> | null,
        strategyCode: raw.strategy_code,
        status: raw.status,
        candidateId,
        candidateDescription,
      },
      trades,
      asOf: new Date().toISOString(),
    };
    return NextResponse.json(payload, {
      headers: { "Cache-Control": "no-store" },
    });
  } catch (err) {
    if (err instanceof BackendError && err.status === 404) {
      const payload: BacktestRunDetailPayload = {
        run: null,
        trades: [],
        asOf: new Date().toISOString(),
      };
      return NextResponse.json(payload, {
        headers: { "Cache-Control": "no-store" },
      });
    }
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

function strOrNull(v: unknown): string | null {
  return typeof v === "string" && v ? v : null;
}
