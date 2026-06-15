"use client";

import { cn } from "@/lib/cn";

import { shortTimestamp } from "./format";
import { Pnl, Sparkline } from "./primitives";

/**
 * 市场归因域工具视图(D-12 行情归因):全市场快讯 / 板块榜 / 资金流向 / 强势股题材。
 * 字段对齐 services/data schemas(MarketNews/SectorBoard/Moneyflow/StrongStocks)。
 * 每个视图带 shape guard,形态不符回 null 由通用 ToolOutput 兜底。
 */

function isObj(v: unknown): v is Record<string, unknown> {
  return !!v && typeof v === "object" && !Array.isArray(v);
}

// ── data.get_market_news:全市场快讯流 ────────────────────────────

interface NewsItem {
  title: string;
  summary?: string;
  published_at?: string | null;
  related_codes?: string[];
}

export function isMarketNews(v: unknown): v is { items: NewsItem[] } {
  if (!isObj(v) || !Array.isArray(v.items) || v.items.length === 0) return false;
  return v.items.every((it) => isObj(it) && typeof it.title === "string");
}

const NEWS_CAP = 8;

export function MarketNewsView({ v }: { v: { items: NewsItem[] } }) {
  const items = v.items.slice(0, NEWS_CAP);
  return (
    <div className="flex flex-col">
      {items.map((n, i) => (
        <div key={i} className="border-b border-border-subtle/50 py-1.5 first:pt-0 last:border-b-0">
          <div className="flex items-baseline gap-2">
            <span className="min-w-0 flex-1 text-[11px] leading-snug text-fg">{n.title}</span>
            {n.published_at && (
              <span className="shrink-0 font-mono text-[9px] tabular-nums text-fg-muted/60">
                {shortTimestamp(n.published_at) ?? ""}
              </span>
            )}
          </div>
          {n.summary && (
            <p className="mt-0.5 line-clamp-2 text-[10px] leading-relaxed text-fg-muted/80">
              {n.summary}
            </p>
          )}
          {n.related_codes && n.related_codes.length > 0 && (
            <div className="mt-0.5 flex flex-wrap gap-1">
              {n.related_codes.slice(0, 6).map((c) => (
                <span
                  key={c}
                  className="rounded-sm border border-border-subtle/70 px-1 font-mono text-[9px] text-fg-muted/70"
                >
                  {c}
                </span>
              ))}
            </div>
          )}
        </div>
      ))}
      {v.items.length > NEWS_CAP && (
        <div className="pt-1 font-mono text-[10px] text-fg-muted/40">
          +{v.items.length - NEWS_CAP} …
        </div>
      )}
    </div>
  );
}

// ── data.get_market_sectors:板块涨跌幅榜 ─────────────────────────

interface SectorItem {
  name: string;
  pct_chg?: number | null;
  leader?: string;
  leader_pct_chg?: number | null;
}

interface SectorBoard {
  top?: SectorItem[];
  bottom?: SectorItem[];
  total_boards?: number;
}

export function isSectorBoard(v: unknown): v is SectorBoard {
  if (!isObj(v) || v.error) return false;
  // 502 回落是 {top:[],bottom:[],error}——空数组也满足 Array.isArray,会渲染出空板;
  // 与 isMarketNews/isMovers 一致,要求至少一侧有数据,否则回落通用 ToolOutput 显示 error。
  return (
    Array.isArray(v.top) &&
    Array.isArray(v.bottom) &&
    (v.top.length > 0 || v.bottom.length > 0)
  );
}

function SectorRow({ s }: { s: SectorItem }) {
  return (
    <div className="flex items-center gap-2 py-0.5">
      <span className="min-w-0 flex-1 truncate text-[11px] text-fg">{s.name}</span>
      {s.leader && (
        <span className="shrink-0 truncate font-mono text-[9px] text-fg-muted/60">{s.leader}</span>
      )}
      {typeof s.pct_chg === "number" && (
        <span className="shrink-0 w-14 text-right">
          <Pnl value={s.pct_chg} suffix="%" />
        </span>
      )}
    </div>
  );
}

export function SectorBoardView({ v }: { v: SectorBoard }) {
  const top = (v.top ?? []).slice(0, 6);
  const bottom = (v.bottom ?? []).slice(0, 6);
  return (
    <div className="flex flex-col gap-2">
      {typeof v.total_boards === "number" && (
        <div className="font-mono text-[9px] uppercase tracking-wider text-fg-muted/50">
          {v.total_boards} 板块
        </div>
      )}
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
        <div>
          <div className="mb-0.5 font-mono text-[9px] uppercase tracking-wider text-bull/70">领涨</div>
          {top.map((s, i) => (
            <SectorRow key={i} s={s} />
          ))}
        </div>
        <div>
          <div className="mb-0.5 font-mono text-[9px] uppercase tracking-wider text-fox-red/70">领跌</div>
          {bottom.map((s, i) => (
            <SectorRow key={i} s={s} />
          ))}
        </div>
      </div>
    </div>
  );
}

// ── data.get_market_moneyflow:沪深港通资金流向 ───────────────────

interface MoneyflowPoint {
  time: string;
  hgt?: number | null;
  sgt?: number | null;
}

interface Moneyflow {
  as_of_time?: string | null;
  hgt_net_yi_cny?: number | null;
  sgt_net_yi_cny?: number | null;
  north_net_yi_cny?: number | null;
  series_sample?: MoneyflowPoint[];
  note?: string;
}

export function isMoneyflow(v: unknown): v is Moneyflow {
  return isObj(v) && ("north_net_yi_cny" in v || "hgt_net_yi_cny" in v);
}

export function MoneyflowView({ v }: { v: Moneyflow }) {
  const series = (v.series_sample ?? [])
    .map((p) => (p.hgt ?? 0) + (p.sgt ?? 0))
    .filter((n) => Number.isFinite(n));
  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-baseline gap-3">
        {typeof v.north_net_yi_cny === "number" && (
          <span className="text-[11px]">
            北向 <Pnl value={v.north_net_yi_cny} suffix=" 亿" />
          </span>
        )}
        {typeof v.hgt_net_yi_cny === "number" && (
          <span className="font-mono text-[10px] text-fg-muted">
            沪 <Pnl value={v.hgt_net_yi_cny} suffix="" />
          </span>
        )}
        {typeof v.sgt_net_yi_cny === "number" && (
          <span className="font-mono text-[10px] text-fg-muted">
            深 <Pnl value={v.sgt_net_yi_cny} suffix="" />
          </span>
        )}
        {v.as_of_time && (
          <span className="ml-auto font-mono text-[9px] text-fg-muted/50">{v.as_of_time}</span>
        )}
      </div>
      {series.length >= 2 && <Sparkline values={series} />}
      {v.note && <p className="text-[9px] leading-relaxed text-fg-muted/50">{v.note}</p>}
    </div>
  );
}

// ── data.get_market_movers:强势股 + 题材标签 ─────────────────────

interface MoverItem {
  code: string;
  name: string;
  reason?: string;
  tags?: string[];
}

export function isMovers(v: unknown): v is { items: MoverItem[] } {
  if (!isObj(v) || !Array.isArray(v.items) || v.items.length === 0) return false;
  return v.items.every(
    (it) => isObj(it) && typeof it.code === "string" && typeof it.name === "string",
  ) && !("title" in (v.items[0] as object));
}

const MOVERS_CAP = 12;

export function MoversView({ v }: { v: { items: MoverItem[] } }) {
  const items = v.items.slice(0, MOVERS_CAP);
  return (
    <div className="flex flex-col gap-1">
      {items.map((m, i) => (
        <div key={i} className="flex items-center gap-2">
          <span className="shrink-0 text-[11px] text-fg">{m.name}</span>
          <span className="shrink-0 font-mono text-[9px] text-fg-muted/50">{m.code}</span>
          {(m.tags && m.tags.length > 0
            ? m.tags
            : m.reason
              ? m.reason.split("+")
              : []
          )
            .slice(0, 4)
            .map((tag, j) => (
              <span
                key={j}
                className={cn(
                  "shrink-0 rounded-sm border border-gold/30 bg-gold/5 px-1 py-px font-mono text-[9px] text-gold",
                )}
              >
                {tag.trim()}
              </span>
            ))}
        </div>
      ))}
      {v.items.length > MOVERS_CAP && (
        <div className="pt-0.5 font-mono text-[10px] text-fg-muted/40">
          +{v.items.length - MOVERS_CAP} …
        </div>
      )}
    </div>
  );
}
