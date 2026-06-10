import { NextRequest, NextResponse } from "next/server";

import { backendFetch } from "@/lib/backend";
import type {
  FactorEffectiveness,
  FactorSpec,
  FactorsPayload,
} from "@/lib/types";

export const dynamic = "force-dynamic";

interface CatalogResp {
  factors: FactorSpec[];
  sources: Record<string, boolean>;
}
interface SnapshotResp {
  venue: string;
  symbol: string;
  timeframe: string;
  as_of: string | null;
  bars_used: number;
  available: boolean;
  reason: string | null;
  top_factors: FactorEffectiveness[];
}

/**
 * GET /api/factors[?venue=&symbol=&timeframe=] —— 因子目录 + 可选有效性快照。
 *
 * **不带 `symbol` 时只回 catalog**(GET /factor/catalog,毫秒级)——因子库页用,
 * 不再为没人看的快照白等 30s 计算。带 `symbol` 才算该标的的 effectiveness
 * (POST /factor/snapshot,拉历史 bar + 算 Rank IC,重)——模拟盘详情用。
 * factor 服务没起 → catalogOk=false;标的算不出 → available=false 带 reason,不静默。
 */
export async function GET(req: NextRequest) {
  const sp = req.nextUrl.searchParams;
  const symbol = sp.get("symbol");
  const venue = sp.get("venue") ?? "binance";
  const timeframe = sp.get("timeframe") ?? "1h";

  const catalogP = backendFetch<CatalogResp>("factor", "/catalog", {
    // catalog 静态目录,dev 端不需要 JWT。
    auth: false,
    timeoutMs: 6000,
  });
  const snapP = symbol
    ? backendFetch<SnapshotResp>("factor", "/snapshot", {
        // snapshot 内部要调 data-service 取 K 线,factor 会转发这个 token,故需 auth。
        auth: true,
        method: "POST",
        timeoutMs: 30_000, // 有效性计算要拉历史 bar + 算 Rank IC,给足时间
        body: {
          venue,
          symbol,
          timeframe,
          lookback_bars: 720,
          horizon_bars: 5,
          top_n: 15,
        },
      })
    : null;

  const [catalogR, snapR] = await Promise.allSettled([
    catalogP,
    snapP ?? Promise.resolve(null),
  ]);

  const catalogOk = catalogR.status === "fulfilled";
  const catalog = catalogOk ? catalogR.value.factors : [];
  const sources = catalogOk ? catalogR.value.sources : {};

  // 没请求快照 → effectiveness=null(前端不渲染);请求了就给可用/不可用的明确结果。
  let effectiveness: FactorsPayload["effectiveness"] = null;
  if (symbol) {
    if (snapR.status === "fulfilled" && snapR.value) {
      const s = snapR.value;
      effectiveness = {
        venue: s.venue ?? venue,
        symbol: s.symbol ?? symbol,
        timeframe: s.timeframe ?? timeframe,
        available: Boolean(s.available),
        reason: s.reason ?? null,
        bars_used: s.bars_used ?? 0,
        as_of: s.as_of ?? null,
        top_factors: Array.isArray(s.top_factors) ? s.top_factors : [],
      };
    } else {
      // snapshot 调用本身失败(服务没起 / 超时)→ 标 unavailable + 原因。
      effectiveness = {
        venue,
        symbol,
        timeframe,
        available: false,
        reason:
          snapR.status === "rejected" && snapR.reason instanceof Error
            ? snapR.reason.message
            : "snapshot 不可用",
        bars_used: 0,
        as_of: null,
        top_factors: [],
      };
    }
  }

  const payload: FactorsPayload = {
    catalog,
    sources,
    effectiveness,
    catalogOk,
    asOf: new Date().toISOString(),
  };
  return NextResponse.json(payload, {
    headers: { "Cache-Control": "no-store" },
  });
}
