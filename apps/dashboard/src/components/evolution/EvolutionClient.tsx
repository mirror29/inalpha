"use client";

import { useTranslations } from "next-intl";
import useSWR from "swr";

import { jsonFetcher } from "@/lib/fetcher";
import type {
  EvolutionCampaignPayload,
  EvolutionLoopPayload,
} from "@/lib/types";
import { evolutionOverview } from "@/lib/evolution-presentation";
import { useEvolutionRuns } from "@/lib/use-evolution-runs";
import { ErrorState, SkeletonBlock } from "@/components/ui/Feedback";
import { LiveStrip } from "@/components/ui/LiveStrip";
import { PageHeader } from "@/components/ui/PageHeader";
import { EvolutionRunTable } from "./EvolutionRunTable";
import { Link } from "@/i18n/navigation";
import { EvolutionCampaignTable } from "./EvolutionCampaignTable";
import { EvolutionLoopTable } from "./EvolutionLoopTable";

/** E1 策略演化运行列表。 */
export function EvolutionClient() {
  const t = useTranslations("evolution");
  const common = useTranslations("common");
  const campaigns = useSWR<EvolutionCampaignPayload>(
    "/api/evolution/campaigns",
    jsonFetcher,
    { refreshInterval: 10_000, keepPreviousData: true },
  );
  const loops = useSWR<EvolutionLoopPayload>(
    "/api/evolution/loops",
    jsonFetcher,
    { refreshInterval: 10_000, keepPreviousData: true },
  );
  const { runs, data: runPages, asOf, error, mutate, hasMore, isLoading,
    isValidating, isLoadingMore, loadMore } = useEvolutionRuns();
  const workflows = loops.data?.loops ?? [];
  const overview = evolutionOverview(workflows, campaigns.data?.campaigns ?? [], runs);
  const history = overview.independent;
  const hasData = Boolean(loops.data || campaigns.data || runPages?.length);
  const completeCoverage = Boolean(loops.data && campaigns.data && runPages?.length);
  const hasError = Boolean(loops.error || campaigns.error || error);
  const retryFailedSources = () => Promise.allSettled([
    ...(loops.error ? [loops.mutate()] : []),
    ...(campaigns.error ? [campaigns.mutate()] : []),
    ...(error ? [mutate()] : []),
  ]);
  const stats = ["loadedTasks", "researching", "observing", "ready"] as const;
  const timestamps = [loops.data?.asOf, campaigns.data?.asOf, asOf]
    .filter((value): value is string => Boolean(value)).sort();
  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title={t("title")}
        subtitle={t("subtitle")}
        right={
          <LiveStrip
            asOf={timestamps[0] ?? new Date().toISOString()}
            isValidating={loops.isValidating || campaigns.isValidating || isValidating}
            isStaleFrame={Boolean(loops.error || campaigns.error || error) || !completeCoverage}
          />
        }
      />
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm text-fg-muted">{t("workflow.scope")}</p>
        <Link
          href="/lab"
          className="rounded-md border border-cyan/40 px-4 py-2 text-sm text-cyan hover:bg-cyan/5"
        >
          {t("workflow.chooseStrategy")} →
        </Link>
      </div>
      {hasData && (
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          {stats.map((key) => (
            <div
              key={key}
              className="rounded-xl border border-border-subtle bg-bg-elev/30 p-4"
            >
              <p className="text-xs text-fg-muted">{t(`workflow.${key}`)}</p>
              <p className="mt-2 font-mono text-2xl">{overview[key]}</p>
            </div>
          ))}
        </div>
      )}
      {!completeCoverage && <p role="status" className="text-sm text-fg-muted">{t("workflow.partial")}</p>}
      {hasError && (hasData ? (
        <div role="alert" className="flex items-center justify-between gap-3 rounded-lg border border-gold/30 p-3 text-sm">
          <p className="text-fg-muted">{t("loop.refreshFailed")}</p>
          <button type="button" onClick={() => void retryFailedSources()} className="shrink-0 text-cyan hover:underline">{common("retry")}</button>
        </div>
      ) : <ErrorState message={t("loop.refreshFailed")} onRetry={() => void retryFailedSources()} />)}
      {loops.isLoading && !loops.data ? (
        <SkeletonBlock className="h-32" />
      ) : workflows.length > 0 ? (
        <EvolutionLoopTable loops={workflows} />
      ) : null}
      {isLoading && !runPages?.length ? <SkeletonBlock className="h-32" /> : !error || runPages?.length ? <EvolutionRunTable
        runs={history.runs}
        hasMore={hasMore}
        isLoadingMore={isLoadingMore}
        onLoadMore={() => void loadMore()}
      /> : null}
      {history.campaigns.length > 0 && (
        <EvolutionCampaignTable campaigns={history.campaigns} />
      )}
    </div>
  );
}
