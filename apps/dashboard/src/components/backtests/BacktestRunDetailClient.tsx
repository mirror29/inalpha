"use client";

import { useLocale, useTranslations } from "next-intl";
import { ArrowLeft } from "lucide-react";
import useSWR from "swr";

import type { BacktestRunDetailPayload } from "@/lib/types";
import { Link } from "@/i18n/navigation";
import { jsonFetcher } from "@/lib/fetcher";
import { ErrorState, SkeletonBlock } from "@/components/ui/Feedback";
import { Panel } from "@/components/ui/Panel";
import {
  BacktestChart,
  BacktestMeta,
  BacktestTradesPanel,
} from "@/components/lab/BacktestPanels";
import { MetricsGrid } from "@/components/lab/MetricsGrid";

/**
 * 单次回测详情页 —— 「Agent 活动」流点击回测事件的落地页。
 * 候选回测从活动流直接去 /lab/[id](信息更全);本页主要服务**内置策略**
 * (sma_cross 等,无候选详情页)与按 runId 直达的复盘场景。
 * 回测是终态记录,不轮询。
 */
export function BacktestRunDetailClient({ runId }: { runId: string }) {
  const t = useTranslations("backtests");
  const tLab = useTranslations("lab.detail");
  const locale = useLocale();

  const { data, error, isLoading, mutate } = useSWR<BacktestRunDetailPayload>(
    `/api/backtests/${runId}`,
    jsonFetcher,
    { revalidateOnFocus: false },
  );

  if (isLoading && !data) {
    return (
      <div className="flex flex-col gap-6">
        <SkeletonBlock className="h-16 border-0 bg-bg-elev/40" />
        <SkeletonBlock className="h-80" />
      </div>
    );
  }
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
      <Link
        href="/activity"
        className="inline-flex w-fit items-center gap-1.5 font-mono text-xs text-fg-muted transition-colors hover:text-cyan"
      >
        <ArrowLeft className="size-3.5" />
        {t("back")}
      </Link>

      {run === null ? (
        <div className="rounded-xl border border-dashed border-border-subtle py-20 text-center text-sm text-fg-muted">
          {t("notFound")}
        </div>
      ) : (
        <>
          <header className="flex flex-col gap-2 border-b border-border-subtle pb-5">
            <h1 className="font-display text-2xl text-fg lg:text-3xl">
              {run.candidateDescription || run.strategyCode}
            </h1>
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1 font-mono text-xs text-fg-muted">
              <span className="uppercase tracking-wider">{run.status}</span>
              <span>{run.runId.slice(0, 8)}</span>
              {run.candidateId && (
                <Link
                  href={`/lab/${run.candidateId}`}
                  className="text-cyan transition-colors hover:text-cyan/80 hover:underline"
                >
                  {t("viewCandidate")} →
                </Link>
              )}
            </div>
          </header>

          <Panel title={tLab("metrics")}>
            <BacktestMeta run={run} locale={locale} />
            <div className="p-4">
              <MetricsGrid
                metrics={run.metrics}
                fitness={
                  typeof run.metrics?.fitness === "number"
                    ? run.metrics.fitness
                    : null
                }
              />
            </div>
          </Panel>

          <BacktestChart run={run} trades={data.trades} />
          <BacktestTradesPanel trades={data.trades} locale={locale} />
        </>
      )}
    </div>
  );
}
