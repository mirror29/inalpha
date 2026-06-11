"use client";

import { cn } from "@/lib/cn";

import { fmtSigned } from "./format";

/**
 * 工具视图共享原语 —— 状态徽章 / 指标格 / 迷你走势图 / 盈亏数字 / 折叠区,
 * 全部贴「印章终端」主题(bull / fox-red / gold / cyan + mono tabular)。
 */

/** 业务状态 → 颜色档:运行·通过=绿,候选·进行=金,错误·拒绝=红,其余中性。 */
const STATUS_TONE: Record<string, string> = {
  running: "text-bull border-bull/40 bg-bull/10",
  promoted: "text-bull border-bull/40 bg-bull/10",
  ok: "text-bull border-bull/40 bg-bull/10",
  filled: "text-bull border-bull/40 bg-bull/10",
  completed: "text-bull border-bull/40 bg-bull/10",
  candidate: "text-gold border-gold/40 bg-gold/10",
  pending: "text-gold border-gold/40 bg-gold/10",
  errored: "text-fox-red border-fox-red/40 bg-fox-red/10",
  error: "text-fox-red border-fox-red/40 bg-fox-red/10",
  rejected: "text-fox-red border-fox-red/40 bg-fox-red/10",
  failed: "text-fox-red border-fox-red/40 bg-fox-red/10",
  stopped: "text-fg-muted border-border-subtle bg-bg/40",
};

export function StatusBadge({ status }: { status: string }) {
  return (
    <span
      className={cn(
        "rounded-sm border px-1.5 py-px font-mono text-[10px] uppercase tracking-wider",
        STATUS_TONE[status.toLowerCase()] ??
          "border-border-subtle bg-bg/40 text-fg-muted",
      )}
    >
      {status}
    </span>
  );
}

/** 指标格:小标题 + mono 数值,3 列网格摆放。 */
export function MetricGrid({
  items,
}: {
  items: { label: string; value: React.ReactNode }[];
}) {
  if (items.length === 0) return null;
  return (
    <div className="grid grid-cols-3 gap-1.5">
      {items.map((m) => (
        <div
          key={m.label}
          className="rounded-sm border border-border-subtle/60 bg-bg/40 px-1.5 py-1"
        >
          <div className="font-mono text-[9px] uppercase tracking-wider text-fg-muted/50">
            {m.label}
          </div>
          <div className="mt-0.5 truncate font-mono text-[11px] tabular-nums text-fg">
            {m.value}
          </div>
        </div>
      ))}
    </div>
  );
}

/** 盈亏 / 带方向数字:正绿负红零中性。 */
export function Pnl({ value, suffix = "" }: { value: number; suffix?: string }) {
  return (
    <span
      className={cn(
        "font-mono tabular-nums",
        value > 0 ? "text-bull" : value < 0 ? "text-fox-red" : "text-fg-muted",
      )}
    >
      {fmtSigned(value)}
      {suffix}
    </span>
  );
}

/** 迷你走势图(SVG polyline):首尾对比定涨跌色;数据 <2 点不画。 */
export function Sparkline({
  values,
  className,
}: {
  values: number[];
  className?: string;
}) {
  const pts = values.filter((v) => Number.isFinite(v));
  if (pts.length < 2) return null;
  const W = 240;
  const H = 36;
  const P = 2;
  const min = Math.min(...pts);
  const max = Math.max(...pts);
  const span = max - min || 1;
  const path = pts
    .map(
      (v, i) =>
        `${P + (i * (W - 2 * P)) / (pts.length - 1)},${
          H - P - ((v - min) / span) * (H - 2 * P)
        }`,
    )
    .join(" ");
  const up = pts[pts.length - 1] >= pts[0];
  return (
    <svg
      viewBox={`0 0 ${W} ${H}`}
      preserveAspectRatio="none"
      aria-hidden
      className={cn("h-9 w-full", className)}
    >
      <polyline
        points={path}
        fill="none"
        strokeWidth="1.5"
        vectorEffect="non-scaling-stroke"
        className={up ? "stroke-bull" : "stroke-fox-red"}
      />
    </svg>
  );
}

/** 标的头:SYMBOL 主体 + venue / timeframe 等小标签。 */
export function SymbolHeader({
  symbol,
  tags,
  right,
}: {
  symbol: string;
  tags?: (string | null | undefined)[];
  right?: React.ReactNode;
}) {
  return (
    <div className="flex items-baseline gap-1.5">
      <span className="font-mono text-[12px] text-fg">{symbol}</span>
      {(tags ?? [])
        .filter((t): t is string => !!t)
        .map((t) => (
          <span
            key={t}
            className="rounded-sm border border-border-subtle/70 px-1 py-px font-mono text-[9px] uppercase tracking-wider text-fg-muted/70"
          >
            {t}
          </span>
        ))}
      {right && <span className="ml-auto">{right}</span>}
    </div>
  );
}

/** 折叠次要区(运行日志 / 代码等):summary 小标题 + 条数提示。 */
export function CollapseSection({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <details className="min-w-0">
      <summary className="flex cursor-pointer items-baseline gap-2 rounded-sm py-px font-mono text-[10px] uppercase tracking-wider text-fg-muted/60 hover:bg-bg-elev/40 hover:text-fg-muted">
        {label}
        {hint && <span className="normal-case tracking-normal text-fg-muted/40">{hint}</span>}
      </summary>
      <div className="ml-2 mt-0.5 border-l border-border-subtle/60 pl-2">
        {children}
      </div>
    </details>
  );
}
