import { NextRequest, NextResponse } from "next/server";

import { backendFetch, BackendError } from "@/lib/backend";
import type { BarPoint, BarsPayload } from "@/lib/types";

export const dynamic = "force-dynamic";

const DEFAULT_LIMIT = 300;

/** timeframe → 秒;不认识返回 null。 */
function timeframeSeconds(tf: string): number | null {
  const m = /^(\d+)(m|h|d|wk|mo)$/.exec(tf);
  if (!m) return null;
  const n = Number(m[1]);
  const unit: Record<string, number> = {
    m: 60,
    h: 3600,
    d: 86_400,
    wk: 604_800,
    mo: 2_592_000,
  };
  const u = unit[m[2]];
  return u ? n * u : null;
}

/**
 * GET /api/bars?venue=&symbol=&timeframe=&limit= —— 最近 N 根 K 线。
 *
 * data /bars 是闭区间查询(from_ts/to_ts 必填),这里按 timeframe 把"最近 N 根"
 * 换算成时间窗口(to=now, from=now - N×tf)再查。给 Live Runner 详情叠图用。
 */
export async function GET(req: NextRequest) {
  const sp = req.nextUrl.searchParams;
  const venue = sp.get("venue");
  const symbol = sp.get("symbol");
  const timeframe = sp.get("timeframe") ?? "1h";
  const limit = Math.min(Number(sp.get("limit")) || DEFAULT_LIMIT, 1000);

  if (!venue || !symbol) {
    return NextResponse.json(
      { error: "venue 和 symbol 必填" },
      { status: 400 },
    );
  }

  const tfSec = timeframeSeconds(timeframe);
  if (!tfSec) {
    return NextResponse.json(
      { error: `无法识别的 timeframe: ${timeframe}` },
      { status: 400 },
    );
  }

  const nowMs = Date.now();
  // 多取一点窗口(×1.5)以防非交易时段/缺口导致根数不足。
  const fromMs = nowMs - limit * tfSec * 1500;
  const fromTs = new Date(fromMs).toISOString();
  const toTs = new Date(nowMs).toISOString();

  const readBars = () =>
    backendFetch<BarPoint[]>("data", "/bars", {
      query: { venue, symbol, timeframe, from_ts: fromTs, to_ts: toTs, limit },
      timeoutMs: 15_000,
    });
  const sortAsc = (bs: BarPoint[]) =>
    [...bs].sort((a, b) => +new Date(a.ts) - +new Date(b.ts));

  try {
    // 先读已落库的 bar。data 不主动回填 —— 切到没回填过的 timeframe（如某标的的 1d）
    // 或窗口偏旧时会空/缺 → 图上"时间是空的"。
    let sorted = sortAsc(await readBars());

    // 新鲜度判定（§3.1 fresh=True）：空 或 最后一根距今 > 2×tf → 幂等回填该窗口再读，
    // 保证 K 线完整且到当前。**只在缺/旧时回填**：数据已新鲜时直接返回，避免每次轮询都
    // 重拉外部源（1d 回填实测可达 ~36s，不能每 20s 白跑）。best-effort：venue 不支持该
    // tf 或外部源失败时不致命，仍用已有数据降级显示。
    const lastMs = sorted.length ? +new Date(sorted[sorted.length - 1].ts) : 0;
    const stale = sorted.length === 0 || nowMs - lastMs > 2 * tfSec * 1000;
    if (stale) {
      try {
        await backendFetch("data", "/backfill/bars", {
          method: "POST",
          timeoutMs: 45_000,
          body: { venue, symbol, timeframe, from_ts: fromTs, to_ts: toTs },
        });
        sorted = sortAsc(await readBars());
      } catch {
        // 忽略：回填失败仍用库里已有的 bar。
      }
    }

    const payload: BarsPayload = {
      venue,
      symbol,
      timeframe,
      bars: sorted.slice(-limit),
      asOf: new Date().toISOString(),
    };
    return NextResponse.json(payload, {
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
