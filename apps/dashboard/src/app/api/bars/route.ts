import { NextRequest, NextResponse } from "next/server";

import { backendFetch, BackendError } from "@/lib/backend";
import type { BarPoint, BarsPayload } from "@/lib/types";

export const dynamic = "force-dynamic";

const DEFAULT_LIMIT = 300;

/**
 * 进程级在途回填去重 —— 慢周期(1d 实测 ~36s)回填 > SWR 轮询间隔(20s),否则同一
 * venue/symbol/tf 会在前一次还没回来时又发一次,两个请求都走进 stale 分支并发回填。
 * 这里用 key→Promise 把同 key 的并发回填合并成一次,后到者复用同一 Promise。
 */
const inflightBackfill = new Map<string, Promise<void>>();

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
 * GET /api/bars?venue=&symbol=&timeframe=&limit=[&from=&to=] —— K 线。
 *
 * 默认"最近 N 根":data /bars 是闭区间查询(from_ts/to_ts 必填),按 timeframe 把
 * "最近 N 根"换算成时间窗口(to=now, from=now - N×tf)再查,给 Live Runner 详情叠图。
 * 传 from/to(ISO)则查**历史固定区间**(回测 K 线用):窗口不随 now 滚动,新鲜度
 * 判定换成"窗口尾部是否齐"——历史区间补齐一次即稳定,不会每次轮询重拉外部源。
 */
export async function GET(req: NextRequest) {
  const sp = req.nextUrl.searchParams;
  const venue = sp.get("venue");
  const symbol = sp.get("symbol");
  const timeframe = sp.get("timeframe") ?? "1h";
  const limit = Math.min(Number(sp.get("limit")) || DEFAULT_LIMIT, 1000);
  const fromParam = sp.get("from");
  const toParam = sp.get("to");

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
  // 历史区间模式:from/to 必须成对且可解析,否则按参数错误拒绝(单传一个多半是调用方 bug)。
  const historical = fromParam !== null || toParam !== null;
  const fromParsed = fromParam ? Date.parse(fromParam) : NaN;
  const toParsed = toParam ? Date.parse(toParam) : NaN;
  if (historical && (Number.isNaN(fromParsed) || Number.isNaN(toParsed))) {
    return NextResponse.json(
      { error: "from/to 需成对提供且为可解析的 ISO 时间" },
      { status: 400 },
    );
  }
  // 多取一点窗口(×1.5)以防非交易时段/缺口导致根数不足。
  const fromMs = historical ? fromParsed : nowMs - limit * tfSec * 1500;
  const toMs = historical ? Math.min(toParsed, nowMs) : nowMs;
  const fromTs = new Date(fromMs).toISOString();
  const toTs = new Date(toMs).toISOString();

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
    // 历史区间:窗口尾部缺 2×tf 以上才算不齐(补一次即稳定);滚动窗口:距 now 判旧。
    const staleEdgeMs = historical ? toMs : nowMs;
    const stale = sorted.length === 0 || staleEdgeMs - lastMs > 2 * tfSec * 1000;
    if (stale) {
      try {
        // 同 key 已有在途回填则复用,不再并发发第二次(见 inflightBackfill 注释)。
        // 历史区间把窗口并进 key:不同回测区间互不干扰;滚动窗口仍按标的去重
        // (from 随 now 漂移,进 key 会让去重失效)。
        const key = historical
          ? `${venue}|${symbol}|${timeframe}|${fromTs}|${toTs}`
          : `${venue}|${symbol}|${timeframe}`;
        let pending = inflightBackfill.get(key);
        if (!pending) {
          pending = backendFetch("data", "/backfill/bars", {
            method: "POST",
            timeoutMs: 45_000,
            body: { venue, symbol, timeframe, from_ts: fromTs, to_ts: toTs },
          }).then(() => undefined);
          inflightBackfill.set(key, pending);
          // finally 链返回的 Promise 在 backfill reject 时本身也 rejected,下方只 await pending、
          // 不消费它 → Node 15+ 会升级成 unhandledRejection;补 .catch 吞掉(实际错误仍由
          // await pending 的 try/catch 处理)（CR）。
          pending.finally(() => inflightBackfill.delete(key)).catch(() => {});
        }
        await pending;
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
