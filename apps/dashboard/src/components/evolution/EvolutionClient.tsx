"use client";

import { useTranslations } from "next-intl";
import useSWR from "swr";

import { jsonFetcher } from "@/lib/fetcher";
import type { EvolutionCampaignPayload } from "@/lib/types";
import { isEvolutionActive } from "@/lib/evolution";
import { useEvolutionRuns } from "@/lib/use-evolution-runs";
import { ErrorState, SkeletonBlock } from "@/components/ui/Feedback";
import { LiveStrip } from "@/components/ui/LiveStrip";
import { PageHeader } from "@/components/ui/PageHeader";
import { EvolutionRunTable } from "./EvolutionRunTable";
import { EvolutionStats } from "./EvolutionStats";
import { EvolutionCampaignTable } from "./EvolutionCampaignTable";

/** E1 策略演化运行列表。 */
export function EvolutionClient() {
  const t = useTranslations("evolution");
  const campaigns = useSWR<EvolutionCampaignPayload>(
    "/api/evolution/campaigns",
    jsonFetcher,
    { refreshInterval: 10_000, keepPreviousData: true },
  );
  const {
    runs,
    asOf,
    error,
    isValidating,
    isLoading,
    mutate,
    hasMore,
    isLoadingMore,
    loadMore,
  } = useEvolutionRuns();
  if (isLoading && runs.length === 0) {
    return <div className="flex flex-col gap-6"><SkeletonBlock className="h-16 w-72 border-0 bg-bg-elev/40" /><SkeletonBlock className="h-96" /></div>;
  }
  if (error && runs.length === 0) {
    return <ErrorState message={error instanceof Error ? error.message : String(error)} onRetry={() => mutate()} />;
  }
  const active = runs.filter((run) => isEvolutionActive(run.status)).length;
  return (
    <div className="flex flex-col gap-6">
      <PageHeader title={t("title")} subtitle={t("subtitle")} right={<LiveStrip asOf={asOf ?? new Date().toISOString()} isValidating={isValidating} isStaleFrame={Boolean(error)} />} />
      <EvolutionStats total={runs.length} active={active} cost={runs.reduce((sum, run) => sum + run.llm_cost_usd, 0)} rejected={runs.reduce((sum, run) => sum + run.rejected, 0)} />
      <EvolutionCampaignTable campaigns={campaigns.data?.campaigns ?? []} />
      <EvolutionRunTable runs={runs} hasMore={hasMore} isLoadingMore={isLoadingMore} onLoadMore={() => void loadMore()} />
    </div>
  );
}
