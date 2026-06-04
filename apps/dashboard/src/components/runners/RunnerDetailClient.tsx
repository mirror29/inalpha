"use client";

import { useLocale, useNow, useTranslations } from "next-intl";
import { ArrowLeft, TriangleAlert } from "lucide-react";
import useSWR from "swr";

import type {
  RunDetailPayload,
  StrategyRunDecisionRecord,
  StrategyRunRecord,
} from "@/lib/types";
import { Link } from "@/i18n/navigation";
import { cn } from "@/lib/cn";
import { fmtRelative, fmtSigned, pnlColor } from "@/lib/format";
import { jsonFetcher } from "@/lib/fetcher";
import { ErrorState, SkeletonBlock } from "@/components/ui/Feedback";
import { LiveStrip } from "@/components/ui/LiveStrip";
import { Panel } from "@/components/ui/Panel";
import { RunStatusBadge } from "@/components/ui/StatusBadge";
import { DecisionTimeline } from "./DecisionTimeline";
import { RunnerChart } from "./RunnerChart";

const REFRESH_MS = 6000;

export function RunnerDetailClient({ runId }: { runId: string }) {
  const t = useTranslations("runners.detail");
  const locale = useLocale();

  const { data, error, isValidating, isLoading, mutate } =
    useSWR<RunDetailPayload>(`/api/runners/${runId}`, jsonFetcher, {
      refreshInterval: REFRESH_MS,
      keepPreviousData: true,
    });

  if (isLoading && !data) {
    return (
      <div className="flex flex-col gap-6">
        <SkeletonBlock className="h-16 border-0 bg-bg-elev/40" />
        <SkeletonBlock className="h-72" />
      </div>
    );
  }

  // 404(run 不存在)→ fetcher 抛 FetchError;无任何帧时给错误态。
  if (error && !data) {
    return (
      <ErrorState
        message={error instanceof Error ? error.message : String(error)}
        onRetry={() => mutate()}
      />
    );
  }

  if (!data) return null;
  const run = data.run;

  return (
    <div className="flex flex-col gap-6">
      <BackLink label={t("back")} />

      {run === null ? (
        <div className="rounded-xl border border-dashed border-border-subtle py-20 text-center text-sm text-fg-muted">
          {t("notFound")}
        </div>
      ) : (
        <>
          {/* 头部:标的 + 状态 + 累计盈亏 + LIVE */}
          <header className="flex flex-col gap-4 border-b border-border-subtle pb-5 lg:flex-row lg:items-end lg:justify-between">
            <div>
              <div className="flex items-center gap-3">
                <h1 className="font-display text-3xl text-fg lg:text-4xl">
                  {run.symbol}
                </h1>
                <RunStatusBadge status={run.status} />
              </div>
              <div className="mt-1.5 font-mono text-xs text-fg-muted">
                {run.venue} · {run.timeframe} ·{" "}
                <span title={run.candidate_id}>
                  cand {run.candidate_id.slice(0, 8)}
                </span>
              </div>
            </div>
            <LiveStrip
              asOf={data.asOf}
              isValidating={isValidating}
              isStaleFrame={Boolean(error)}
            />
          </header>

          {/* 当前模拟盘指标条(置于 K 线上方)。 */}
          <RunnerStats run={run} decisions={data.decisions} />

          {/* K 线置顶 —— 决策点叠在蜡烛上,先看价格走势。 */}
          <RunnerChart
            venue={run.venue}
            symbol={run.symbol}
            timeframe={run.timeframe}
            decisions={data.decisions}
          />

          <DecisionTimeline decisions={data.decisions} />

          {/* 错误日志置底(running 失败的逐次记录)。 */}
          {run.error_log.length > 0 && <ErrorLog entries={run.error_log} />}
        </>
      )}
    </div>
  );
}

/** 当前模拟盘指标条:累计盈亏 / 决策数 / 风控拦截 / 最后 bar。 */
function RunnerStats({
  run,
  decisions,
}: {
  run: StrategyRunRecord;
  decisions: StrategyRunDecisionRecord[];
}) {
  const t = useTranslations("runners.stats");
  const locale = useLocale();
  const now = useNow({ updateInterval: 10_000 });
  const filled = decisions.filter((d) => d.outcome === "filled").length;
  const blocked = decisions.filter((d) => d.outcome === "risk_rejected").length;

  return (
    <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
      <StatCard label={t("cumulativePnl")} accent>
        <Figure className={pnlColor(run.cumulative_pnl)}>
          {fmtSigned(run.cumulative_pnl, null, locale)}
        </Figure>
      </StatCard>
      <StatCard label={t("decisions")}>
        <Figure>{decisions.length}</Figure>
        <Sub>{t("filledOf", { filled })}</Sub>
      </StatCard>
      <StatCard label={t("blocked")}>
        <Figure className={blocked > 0 ? "text-fox-red" : undefined}>
          {blocked}
        </Figure>
      </StatCard>
      <StatCard label={t("lastBar")}>
        <Figure className="text-lg">
          {run.last_bar_ts
            ? fmtRelative(run.last_bar_ts, now.getTime(), locale)
            : t("neverRan")}
        </Figure>
      </StatCard>
    </div>
  );
}

function StatCard({
  label,
  accent,
  children,
}: {
  label: string;
  accent?: boolean;
  children: React.ReactNode;
}) {
  return (
    <div className="relative overflow-hidden rounded-xl border border-border-subtle bg-bg-elev/30 px-4 py-3 backdrop-blur-sm">
      {accent && <span className="absolute inset-x-0 top-0 h-px bg-cyan/50" />}
      <div className="font-mono text-[10px] uppercase tracking-[0.16em] text-fg-muted">
        {label}
      </div>
      <div className="mt-1.5">{children}</div>
    </div>
  );
}

function Figure({
  children,
  className,
}: {
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "tnum font-mono text-2xl leading-none tracking-tight text-fg",
        className,
      )}
    >
      {children}
    </div>
  );
}

function Sub({ children }: { children: React.ReactNode }) {
  return (
    <div className="tnum mt-1 font-mono text-[11px] text-fg-muted/80">
      {children}
    </div>
  );
}

function BackLink({ label }: { label: string }) {
  return (
    <Link
      href="/runners"
      className="inline-flex w-fit items-center gap-1.5 font-mono text-xs text-fg-muted transition-colors hover:text-cyan"
    >
      <ArrowLeft className="size-3.5" />
      {label}
    </Link>
  );
}

function ErrorLog({ entries }: { entries: Array<Record<string, unknown>> }) {
  const t = useTranslations("runners.detail");
  return (
    <Panel index="2.0" title={t("errorLog")}>
      <ul className="divide-y divide-border-subtle/60">
        {entries.map((e, i) => (
          <li
            key={i}
            className="flex items-start gap-2 px-4 py-2.5 font-mono text-[11px] text-fox-red"
          >
            <TriangleAlert className="mt-0.5 size-3 shrink-0" strokeWidth={2} />
            <span className="break-all text-fg-muted">
              {typeof e === "object"
                ? JSON.stringify(e)
                : String(e)}
            </span>
          </li>
        ))}
      </ul>
    </Panel>
  );
}
