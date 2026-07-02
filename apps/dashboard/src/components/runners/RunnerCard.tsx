"use client";

import { useState } from "react";
import { useLocale, useNow, useTranslations } from "next-intl";
import { ChevronDown, ChevronRight, TriangleAlert } from "lucide-react";

import type { StrategyRunRecord } from "@/lib/types";
import { Link } from "@/i18n/navigation";
import { cn } from "@/lib/cn";
import { fmtDateTime, fmtNum, fmtRelative, fmtSigned, pnlColor } from "@/lib/format";
import { RunStatusBadge } from "@/components/ui/StatusBadge";

/**
 * 单个 Live Runner 卡片 —— 主区可点进决策详情。
 * cumulative_pnl 是**净盈亏**(已实现+未实现-手续费)的 mark-to-market 估算;
 * last_bar_ts 超时(running 但很久没新 bar)标黄。
 *
 * `history`:同一策略(candidate)更早的 run —— 重启会新建 run 记录(审计保留),
 * 列表页按策略分组后只立最新卡,旧 run 折叠在卡底「历史运行 N 次」里,
 * 避免一个策略重启几次就长出几张并排卡片误导成多个策略。
 */
export function RunnerCard({
  run,
  history = [],
}: {
  run: StrategyRunRecord;
  history?: StrategyRunRecord[];
}) {
  const t = useTranslations("runners.card");
  const locale = useLocale();
  const now = useNow({ updateInterval: 10_000 });
  const [historyOpen, setHistoryOpen] = useState(false);

  // 卡片角标只数 error 级(run_log 现含 info/warn 活动,全数会虚高)。
  const errorCount = run.run_log.filter((e) => e.level === "error").length;
  // running 但 last_bar 超过 4× timeframe 没动 → 可能卡住,标黄提示。
  const lastBarStale = isLastBarStale(run, now.getTime());

  return (
    <div
      className={cn(
        "group relative flex flex-col rounded-xl border border-border-subtle bg-bg-elev/30 backdrop-blur-sm transition-colors",
        "hover:border-cyan/40 hover:bg-bg-elev/50",
      )}
    >
      <Link
        href={`/runners/${run.id}`}
        className="flex flex-col gap-3 p-4"
      >
      {/* 顶部:标的 + 状态 */}
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="truncate text-fg">
            {run.symbol}
            <span className="ml-1.5 font-mono text-xs text-fg-muted">
              · {run.venue}
            </span>
            {/* 现货/合约显式标注:perp 金色带杠杆,spot 灰色轻量 */}
            <span
              className={cn(
                "ml-1.5 rounded px-1 py-0.5 font-mono text-[10px]",
                run.trading_mode === "perp"
                  ? "bg-gold/10 text-gold"
                  : "bg-bg-elev text-fg-muted",
              )}
            >
              {run.trading_mode === "perp"
                ? t("modePerp", { leverage: run.leverage })
                : t("modeSpot")}
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
        {/* 资金额度(sizing 上限);老 run 为 null 不显示 */}
        {run.allocation !== null && (
          <Meta label={t("allocation")}>
            <span className="text-fg-muted">
              {fmtNum(run.allocation, locale, 0)}
            </span>
          </Meta>
        )}
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

      {/* 历史运行(重启留下的旧 run)—— 折叠,展开后每行可点进对应 run 详情。 */}
      {history.length > 0 && (
        <div className="border-t border-border-subtle/60 px-4 py-2">
          <button
            type="button"
            onClick={() => setHistoryOpen((v) => !v)}
            aria-expanded={historyOpen}
            className="flex w-full items-center gap-1.5 font-mono text-[11px] text-fg-muted/70 transition-colors hover:text-fg"
          >
            <ChevronDown
              className={cn(
                "size-3 transition-transform",
                historyOpen ? "rotate-0" : "-rotate-90",
              )}
              strokeWidth={2}
            />
            {t("history", { count: history.length })}
          </button>
          {historyOpen && (
            <ul className="mt-1.5 flex flex-col">
              {history.map((h) => (
                <li key={h.id}>
                  <Link
                    href={`/runners/${h.id}`}
                    className="flex items-center gap-2 rounded px-1.5 py-1 font-mono text-[11px] text-fg-muted transition-colors hover:bg-bg/50 hover:text-fg"
                  >
                    <span
                      className={cn(
                        "size-1.5 shrink-0 rounded-full",
                        h.status === "errored" ? "bg-fox-red" : "bg-fg-muted/50",
                      )}
                    />
                    <span className="tnum truncate">
                      {fmtDateTime(h.started_at, locale)}
                      {h.stopped_at
                        ? ` → ${fmtDateTime(h.stopped_at, locale)}`
                        : ""}
                    </span>
                    <span
                      className={cn(
                        "tnum ml-auto shrink-0",
                        pnlColor(h.cumulative_pnl),
                      )}
                    >
                      {fmtSigned(h.cumulative_pnl, null, locale)}
                    </span>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
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
