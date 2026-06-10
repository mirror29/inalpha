"use client";

import { useLocale, useNow, useTranslations } from "next-intl";
import { ChevronRight, TriangleAlert } from "lucide-react";

import type { StrategyRunRecord } from "@/lib/types";
import { Link } from "@/i18n/navigation";
import { cn } from "@/lib/cn";
import { fmtRelative, fmtSigned, pnlColor } from "@/lib/format";
import { RunStatusBadge } from "@/components/ui/StatusBadge";

/**
 * 单个 Live Runner 卡片 —— 整卡可点进决策详情。
 * cumulative_pnl 是**净盈亏**(已实现+未实现-手续费)的 mark-to-market 估算;
 * last_bar_ts 超时(running 但很久没新 bar)标黄。
 */
export function RunnerCard({ run }: { run: StrategyRunRecord }) {
  const t = useTranslations("runners.card");
  const locale = useLocale();
  const now = useNow({ updateInterval: 10_000 });

  // 卡片角标只数 error 级(run_log 现含 info/warn 活动,全数会虚高)。
  const errorCount = run.run_log.filter((e) => e.level === "error").length;
  // running 但 last_bar 超过 4× timeframe 没动 → 可能卡住,标黄提示。
  const lastBarStale = isLastBarStale(run, now.getTime());

  return (
    <Link
      href={`/runners/${run.id}`}
      className={cn(
        "group relative flex flex-col gap-3 rounded-xl border border-border-subtle bg-bg-elev/30 p-4 backdrop-blur-sm transition-colors",
        "hover:border-cyan/40 hover:bg-bg-elev/50",
      )}
    >
      {/* 顶部:标的 + 状态 */}
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="truncate text-fg">
            {run.symbol}
            <span className="ml-1.5 font-mono text-xs text-fg-muted">
              · {run.venue}
            </span>
          </div>
          <div className="mt-0.5 font-mono text-[11px] uppercase tracking-wider text-fg-muted/70">
            {run.timeframe}
          </div>
        </div>
        <RunStatusBadge status={run.status} />
      </div>

      {/* 累计盈亏 */}
      <div>
        <div className="font-mono text-[10px] uppercase tracking-[0.16em] text-fg-muted/70">
          {t("cumulativePnl")}
        </div>
        <div
          className={cn(
            "tnum mt-1 font-mono text-2xl leading-none tracking-tight",
            pnlColor(run.cumulative_pnl),
          )}
        >
          {fmtSigned(run.cumulative_pnl, null, locale)}
        </div>
      </div>

      {/* 元信息 */}
      <dl className="grid grid-cols-2 gap-x-3 gap-y-1.5 font-mono text-[11px]">
        <Meta label={t("lastBar")}>
          <span className={lastBarStale ? "text-gold" : "text-fg-muted"}>
            {run.last_bar_ts
              ? fmtRelative(run.last_bar_ts, now.getTime(), locale)
              : t("neverRan")}
          </span>
        </Meta>
        <Meta label={t("candidate")}>
          <span className="text-fg-muted" title={run.candidate_id}>
            {run.candidate_id.slice(0, 8)}
          </span>
        </Meta>
      </dl>

      {/* 错误角标 */}
      {errorCount > 0 && (
        <div className="flex items-center gap-1.5 rounded border border-fox-red/25 bg-fox-red/[0.07] px-2 py-1 font-mono text-[11px] text-fox-red">
          <TriangleAlert className="size-3" strokeWidth={2} />
          {t("errors", { count: errorCount })}
        </div>
      )}

      <ChevronRight className="absolute right-3 top-3 size-4 text-fg-muted/0 transition-colors group-hover:text-cyan/70" />
    </Link>
  );
}

function Meta({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex flex-col gap-0.5">
      <dt className="uppercase tracking-wider text-fg-muted/50">{label}</dt>
      <dd className="tnum truncate">{children}</dd>
    </div>
  );
}

/** running 且 last_bar 超过 ~4× timeframe 没更新 → 视为停滞。 */
function isLastBarStale(run: StrategyRunRecord, nowMs: number): boolean {
  if (run.status !== "running" || !run.last_bar_ts) return false;
  const tfSec = timeframeSeconds(run.timeframe);
  if (!tfSec) return false;
  const ageSec = (nowMs - new Date(run.last_bar_ts).getTime()) / 1000;
  return ageSec > tfSec * 4;
}

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
  return n * (unit[m[2]] ?? 0) || null;
}
