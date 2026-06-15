"use client";

import { fmtNum, hostOf, shortDate } from "./format";
import { MetricGrid, Pnl } from "./primitives";
import type { ReactNode } from "react";

/**
 * 杂项工具视图:web.fetch 抓取正文 / data.get_fundamentals 基本面 / paper.list_strategies。
 * 字段对齐 services/data schemas 与 paper client。形态不符回 null 由通用 ToolOutput 兜底。
 */

function isObj(v: unknown): v is Record<string, unknown> {
  return !!v && typeof v === "object" && !Array.isArray(v);
}

// ── web.fetch:URL → 可引用正文 ───────────────────────────────────

interface WebFetch {
  url: string;
  final_url?: string | null;
  title?: string | null;
  published_at?: string | null;
  text?: string;
  truncated?: boolean;
  error?: string | null;
}

export function isWebFetch(v: unknown): v is WebFetch {
  return isObj(v) && typeof v.url === "string" && ("text" in v || "final_url" in v);
}

const TEXT_PREVIEW = 360;

export function WebFetchView({ v }: { v: WebFetch }) {
  const host = hostOf(v.final_url || v.url);
  const text = v.text ?? "";
  if (v.error) {
    return (
      <div className="flex flex-col gap-0.5">
        <span className="font-mono text-[10px] text-fg-muted">{host}</span>
        <p className="font-mono text-[11px] text-fox-red">{v.error}</p>
      </div>
    );
  }
  return (
    <div className="flex flex-col gap-1">
      {v.title && <div className="text-[12px] font-medium leading-snug text-fg">{v.title}</div>}
      <div className="flex items-baseline gap-2 font-mono text-[9px] text-fg-muted/60">
        <a
          href={v.final_url || v.url}
          target="_blank"
          rel="noopener noreferrer"
          className="truncate text-cyan/80 hover:text-cyan hover:underline"
        >
          {host}
        </a>
        {v.published_at && <span>{shortDate(v.published_at)}</span>}
        {v.truncated && <span className="text-gold/70">truncated</span>}
      </div>
      {text && (
        <p className="text-[11px] leading-relaxed text-fg-muted">
          {text.length > TEXT_PREVIEW ? `${text.slice(0, TEXT_PREVIEW)}…` : text}
        </p>
      )}
    </div>
  );
}

// ── data.get_fundamentals:标准化财报基本面 ───────────────────────
// 后端 FinancialsResponse 把 akshare/yfinance 都映射到统一 indicators(见
// services/data schemas)。这里按 估值/盈利/成长/财务 分组渲染,缺失字段跳过。

interface Indicators {
  market_cap?: number | null;
  pe_ratio?: number | null;
  pb_ratio?: number | null;
  roe?: number | null;
  revenue_yoy?: number | null;
  profit_yoy?: number | null;
  gross_margin?: number | null;
  net_margin?: number | null;
  debt_to_equity?: number | null;
}

interface Financials {
  symbol: string;
  venue?: string;
  available?: boolean;
  reason?: string | null;
  as_of?: string | null;
  indicators: Indicators;
}

function indicatorCount(ind: unknown): number {
  if (!isObj(ind)) return 0;
  return Object.values(ind).filter((x) => x != null).length;
}

/** 有 symbol + indicators,且(不可用 或 至少一个指标非空)才渲染;否则回落 ToolOutput。 */
export function isFundamentals(v: unknown): v is Financials {
  if (!isObj(v) || typeof v.symbol !== "string" || !isObj(v.indicators)) return false;
  return v.available === false || indicatorCount(v.indicators) > 0;
}

/** 大数压缩:万亿 / 亿 / 万(货币中性,不带符号)。 */
function fmtCap(v: number): string {
  const a = Math.abs(v);
  if (a >= 1e12) return `${(v / 1e12).toFixed(2)}万亿`;
  if (a >= 1e8) return `${(v / 1e8).toFixed(1)}亿`;
  if (a >= 1e4) return `${(v / 1e4).toFixed(1)}万`;
  return fmtNum(v);
}

/** 比率值归一到百分数:|v|≤1.5 当分数(×100),否则当已是百分数(兼容两个连接器口径)。 */
function asPct(v: number): number {
  return Math.abs(v) <= 1.5 ? v * 100 : v;
}

export function FundamentalsView({ v }: { v: Financials }) {
  const ind = v.indicators;
  const header = (
    <div className="flex items-baseline gap-2">
      <span className="font-mono text-[12px] text-fg">{v.symbol}</span>
      {v.venue && (
        <span className="rounded-sm border border-border-subtle/70 px-1 font-mono text-[9px] uppercase text-fg-muted/70">
          {v.venue}
        </span>
      )}
      {v.as_of && (
        <span className="ml-auto font-mono text-[9px] text-fg-muted/50">{shortDate(v.as_of)}</span>
      )}
    </div>
  );

  if (v.available === false) {
    return (
      <div className="flex flex-col gap-1">
        {header}
        <p className="font-mono text-[11px] text-fox-red">{v.reason ?? "基本面数据不可用"}</p>
      </div>
    );
  }

  const num = (x: number | null | undefined): x is number => typeof x === "number";
  type Cell = { label: string; value: ReactNode };
  const groups: { label: string; items: Cell[] }[] = [
    {
      label: "估值",
      items: [
        num(ind.market_cap) && { label: "市值", value: fmtCap(ind.market_cap) },
        num(ind.pe_ratio) && { label: "市盈率", value: fmtNum(ind.pe_ratio) },
        num(ind.pb_ratio) && { label: "市净率", value: fmtNum(ind.pb_ratio) },
      ].filter(Boolean) as Cell[],
    },
    {
      label: "盈利",
      items: [
        num(ind.roe) && { label: "ROE", value: `${asPct(ind.roe).toFixed(1)}%` },
        num(ind.gross_margin) && { label: "毛利率", value: `${asPct(ind.gross_margin).toFixed(1)}%` },
        num(ind.net_margin) && { label: "净利率", value: `${asPct(ind.net_margin).toFixed(1)}%` },
      ].filter(Boolean) as Cell[],
    },
    {
      label: "成长",
      items: [
        num(ind.revenue_yoy) && {
          label: "营收增速",
          value: <Pnl value={asPct(ind.revenue_yoy)} suffix="%" />,
        },
        num(ind.profit_yoy) && {
          label: "净利增速",
          value: <Pnl value={asPct(ind.profit_yoy)} suffix="%" />,
        },
      ].filter(Boolean) as Cell[],
    },
    {
      label: "财务",
      items: [
        num(ind.debt_to_equity) && { label: "负债/权益", value: fmtNum(ind.debt_to_equity) },
      ].filter(Boolean) as Cell[],
    },
  ].filter((g) => g.items.length > 0);

  return (
    <div className="flex flex-col gap-2">
      {header}
      {groups.map((g) => (
        <div key={g.label}>
          <div className="mb-1 font-mono text-[9px] uppercase tracking-wider text-fg-muted/50">
            {g.label}
          </div>
          <MetricGrid items={g.items} />
        </div>
      ))}
    </div>
  );
}

// ── paper.list_strategies:已注册 strategy_id 列表 ────────────────

export function isStrategyIdList(v: unknown): v is { strategies: string[] } {
  return (
    isObj(v) &&
    Array.isArray(v.strategies) &&
    v.strategies.every((s) => typeof s === "string")
  );
}

export function StrategyIdListView({ v }: { v: { strategies: string[] } }) {
  if (v.strategies.length === 0) {
    return <span className="font-mono text-[11px] text-fg-muted/60">无已注册策略</span>;
  }
  return (
    <div className="flex flex-wrap gap-1">
      {v.strategies.map((s) => (
        <span
          key={s}
          className="rounded-sm border border-cyan/30 bg-cyan/5 px-1.5 py-px font-mono text-[11px] text-cyan"
        >
          {s}
        </span>
      ))}
    </div>
  );
}
