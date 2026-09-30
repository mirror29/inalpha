"use client";

import { useTranslations } from "next-intl";
import useSWR from "swr";

import { jsonFetcher } from "@/lib/fetcher";
import type {
  EvolutionCampaignPayload,
  EvolutionLoopPayload,
} from "@/lib/types";
import { independentExperiments } from "@/lib/evolution-presentation";
import { loopStage } from "@/lib/evolution-loop";
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
  const { runs, asOf, error, mutate, hasMore, isLoadingMore, loadMore } =
    useEvolutionRuns();
  if (loops.isLoading && !loops.data) {
    return (
      <div className="flex flex-col gap-6">
        <SkeletonBlock className="h-16 w-72 border-0 bg-bg-elev/40" />
        <SkeletonBlock className="h-96" />
      </div>
    );
  }
  if (loops.error && !loops.data) {
    return (
      <ErrorState
        message={
          loops.error instanceof Error
            ? loops.error.message
            : String(loops.error)
        }
        onRetry={() => loops.mutate()}
      />
    );
  }
  const workflows = loops.data?.loops ?? [];
  const history = independentExperiments(
    workflows,
    campaigns.data?.campaigns ?? [],
    runs,
  );
  const stats = [
    ["loadedTasks", workflows.length],
    [
      "researching",
      workflows.filter((loop) => {
        const stage = loopStage(loop.status);
        return stage !== null && stage !== 2 && stage !== 4;
      }).length,
    ],
    [
      "observing",
      workflows.filter((loop) => loop.status === "waiting_forward").length,
    ],
    [
      "ready",
      workflows.filter((loop) => loop.status === "adoption_ready").length,
    ],
  ] as const;
  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title={t("title")}
        subtitle={t("subtitle")}
        right={
          <LiveStrip
            asOf={loops.data?.asOf ?? asOf ?? new Date().toISOString()}
            isValidating={loops.isValidating}
            isStaleFrame={Boolean(loops.error)}
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
      {loops.data && (
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          {stats.map(([key, value]) => (
            <div
              key={key}
              className="rounded-xl border border-border-subtle bg-bg-elev/30 p-4"
            >
              <p className="text-xs text-fg-muted">{t(`workflow.${key}`)}</p>
              <p className="mt-2 font-mono text-2xl">{value}</p>
            </div>
          ))}
        </div>
      )}
      {loops.error && (
        <ErrorState
          message={t("loop.refreshFailed")}
          onRetry={() => loops.mutate()}
        />
      )}
      {loops.isLoading ? (
        <SkeletonBlock className="h-32" />
      ) : (
        <EvolutionLoopTable loops={loops.data?.loops ?? []} />
      )}
      {campaigns.error && (
        <ErrorState
          message={t("loop.refreshFailed")}
          onRetry={() => campaigns.mutate()}
        />
      )}
      {!loops.isLoading &&
        !loops.error &&
        !campaigns.isLoading &&
        !campaigns.error && (
          <details className="rounded-xl border border-border-subtle">
            <summary className="cursor-pointer p-4 text-sm text-fg-muted">
              {t("workflow.history")}
            </summary>
            <div className="flex flex-col gap-4 p-4 pt-0">
              <p className="text-sm text-fg-muted">
                {t("workflow.historyHint")}
              </p>
              <EvolutionCampaignTable campaigns={history.campaigns} />
              {error && (
                <ErrorState
                  message={t("loop.refreshFailed")}
                  onRetry={() => mutate()}
                />
              )}
              <EvolutionRunTable
                runs={history.runs}
                hasMore={hasMore}
                isLoadingMore={isLoadingMore}
                onLoadMore={() => void loadMore()}
              />
            </div>
          </details>
        )}
    </div>
  );
}
