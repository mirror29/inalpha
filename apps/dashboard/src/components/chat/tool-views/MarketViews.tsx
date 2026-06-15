"use client";

import { cn } from "@/lib/cn";

import { fmtNum, shortTimestamp } from "./format";
import { Sparkline, SymbolHeader } from "./primitives";

/**
 * 行情类工具视图:data.get_ticker(最新价卡片)/ data.get_bars(走势 + OHLC 表)。
 * shape guard 导出给注册表 —— 形态不符回 null,由通用 ToolOutput 兜底。
 */

export interface TickerShape {
  venue: string;
  symbol: string;
  price: number;
  ts?: string;
  source?: string;
  is_stale?: boolean;
  stale_seconds?: number;
}

export function isTicker(v: unknown): v is TickerShape {
  const o = v as TickerShape;
  return (
    !!o &&
    typeof o === "object" &&
    typeof o.symbol === "string" &&
    typeof o.price === "number"
  );
}

/** 最新价卡片:大号价格 + 标的/venue + 时间 + staleness 提示。 */
export function TickerView({ t }: { t: TickerShape }) {
  return (
    <div className="flex flex-col gap-1">
      <SymbolHeader
        symbol={t.symbol}
        tags={[t.venue]}
        right={
          t.is_stale ? (
            <span className="rounded-sm border border-fox-red/40 bg-fox-red/10 px-1 py-px font-mono text-[9px] uppercase tracking-wider text-fox-red">
              stale {t.stale_seconds != null ? `${t.stale_seconds}s` : ""}
            </span>
          ) : null
        }
      />
      <div className="font-mono text-xl tabular-nums leading-none text-fg">
        {fmtNum(t.price)}
      </div>
      <div className="font-mono text-[10px] text-fg-muted/60">
        {t.ts ? (shortTimestamp(t.ts) ?? t.ts) : null}
        {t.source ? ` · ${t.source}` : null}
      </div>
    </div>
  );
}

export interface BarShape {
  ts: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume?: number;
  symbol?: string;
  venue?: string;
  timeframe?: string;
}

export interface BarsShape {
  bars: BarShape[];
  count?: number;
}

export function isBars(v: unknown): v is BarsShape {
  const o = v as BarsShape;
  return (
    !!o &&
    typeof o === "object" &&
    Array.isArray(o.bars) &&
    o.bars.length > 0 &&
    typeof o.bars[0]?.close === "number"
  );
}

const BARS_ROW_CAP = 12;

/** K 线视图:收盘价走势图 + 紧凑 OHLC 表(行多取尾部最新)。 */
export function BarsView({ d }: { d: BarsShape }) {
  const bars = d.bars;
  const first = bars[0];
  const shown = bars.slice(-BARS_ROW_CAP);
  const hidden = bars.length - shown.length;
  const hasVol = bars.some((b) => typeof b.volume === "number");
  return (
    <div className="flex flex-col gap-1">
      <SymbolHeader
        symbol={first.symbol ?? ""}
        tags={[first.venue, first.timeframe]}
        right={
          <span className="font-mono text-[10px] tabular-nums text-fg-muted/60">
            {bars.length} bars
          </span>
        }
      />
      <Sparkline values={bars.map((b) => b.close)} />
      <div className="overflow-x-auto">
        <table className="w-full border-collapse font-mono text-[10px] tabular-nums">
          <thead>
            <tr className="text-fg-muted/50">
              {["ts", "open", "high", "low", "close", ...(hasVol ? ["vol"] : [])].map(
                (h) => (
                  <th
                    key={h}
                    className={cn(
                      "whitespace-nowrap border-b border-border-subtle px-1 py-0.5 font-normal uppercase tracking-wider",
                      h === "ts" ? "text-left" : "text-right",
                    )}
                  >
                    {h}
                  </th>
                ),
              )}
            </tr>
          </thead>
          <tbody>
            {shown.map((b) => (
              <tr key={b.ts} className="border-b border-border-subtle/40 last:border-b-0">
                <td className="whitespace-nowrap px-1 py-0.5 text-left text-fg-muted">
                  {shortTimestamp(b.ts)?.slice(0, 16) ?? b.ts}
                </td>
                {[b.open, b.high, b.low].map((n, i) => (
                  <td key={i} className="whitespace-nowrap px-1 py-0.5 text-right text-fg-muted">
                    {fmtNum(n)}
                  </td>
                ))}
                <td
                  className={cn(
                    "whitespace-nowrap px-1 py-0.5 text-right",
                    b.close >= b.open ? "text-bull" : "text-fox-red",
                  )}
                >
                  {fmtNum(b.close)}
                </td>
                {hasVol && (
                  <td className="whitespace-nowrap px-1 py-0.5 text-right text-fg-muted/60">
                    {b.volume != null ? fmtNum(b.volume) : "—"}
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
        {hidden > 0 && (
          <div className="py-0.5 font-mono text-[10px] text-fg-muted/40">
            +{hidden} earlier …
          </div>
        )}
      </div>
    </div>
  );
}
