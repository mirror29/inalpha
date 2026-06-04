"use client";

import { useLocale, useTranslations } from "next-intl";
import { ArrowLeft, TriangleAlert } from "lucide-react";
import useSWR from "swr";

import type { RunDetailPayload } from "@/lib/types";
import { Link } from "@/i18n/navigation";
import { cn } from "@/lib/cn";
import { fmtSigned, pnlColor } from "@/lib/format";
import { jsonFetcher } from "@/lib/fetcher";
import { ErrorState, SkeletonBlock } from "@/components/ui/Feedback";
import { LiveStrip } from "@/components/ui/LiveStrip";
import { Panel } from "@/components/ui/Panel";
import { RunStatusBadge } from "@/components/ui/StatusBadge";
import { DecisionTimeline } from "./DecisionTimeline";

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
              <div
                className={cn(
                  "tnum mt-3 font-mono text-2xl leading-none",
                  pnlColor(run.cumulative_pnl),
                )}
              >
                {fmtSigned(run.cumulative_pnl, null, locale)}
              </div>
            </div>
            <LiveStrip
              asOf={data.asOf}
              isValidating={isValidating}
              isStaleFrame={Boolean(error)}
            />
          </header>

          {/* 错误日志(running 失败的逐次记录)*/}
          {run.error_log.length > 0 && <ErrorLog entries={run.error_log} />}

          <DecisionTimeline decisions={data.decisions} />
        </>
      )}
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
