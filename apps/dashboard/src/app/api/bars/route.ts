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

  try {
    const bars = await backendFetch<BarPoint[]>("data", "/bars", {
      query: {
        venue,
        symbol,
        timeframe,
        from_ts: new Date(fromMs).toISOString(),
        to_ts: new Date(nowMs).toISOString(),
        limit,
      },
      timeoutMs: 15_000,
    });

    // 升序排序(图表要求严格递增)+ 取末尾 limit 根。
    const sorted = [...bars].sort((a, b) => +new Date(a.ts) - +new Date(b.ts));
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
