"use client";

import { fmtNum, hostOf, shortDate } from "./format";
import { MetricGrid } from "./primitives";

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

// ── data.get_fundamentals:基本面(字段随市场而异,渲染标量键值格)──

const FUND_SKIP = new Set(["symbol", "venue", "as_of", "currency"]);

/** akshare/yfinance 字段不固定 → 只取顶层标量(数值 / 短字符串)做指标格。 */
function fundamentalItems(v: Record<string, unknown>): { label: string; value: string }[] {
  return Object.entries(v)
    .filter(
      ([k, val]) =>
        !FUND_SKIP.has(k) &&
        (typeof val === "number" ||
          (typeof val === "string" && val.length > 0 && val.length <= 24)),
    )
    .slice(0, 18)
    .map(([k, val]) => ({
      label: k.replace(/_/g, " "),
      value: typeof val === "number" ? fmtNum(val) : String(val),
    }));
}

/** 至少有一个可展示标量才认作可视化基本面(否则回落通用 ToolOutput)。 */
export function isFundamentals(v: unknown): v is Record<string, unknown> {
  return isObj(v) && fundamentalItems(v).length > 0;
}

export function FundamentalsView({ v }: { v: Record<string, unknown> }) {
  const items = fundamentalItems(v);
  const symbol = typeof v.symbol === "string" ? v.symbol : "";
  return (
    <div className="flex flex-col gap-1.5">
      {symbol && <div className="font-mono text-[12px] text-fg">{symbol}</div>}
      <MetricGrid items={items} />
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
