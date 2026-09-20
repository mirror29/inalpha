"use client";

import { useTranslations } from "next-intl";
import useSWR from "swr";

import { Link } from "@/i18n/navigation";
import { loopRefreshInterval, loopStage } from "@/lib/evolution-loop";
import { jsonFetcher } from "@/lib/fetcher";
import type { EvolutionLoopDetailPayload } from "@/lib/types";
import { ErrorState, SkeletonBlock } from "@/components/ui/Feedback";
import { LiveStrip } from "@/components/ui/LiveStrip";
import { PageHeader } from "@/components/ui/PageHeader";
import { Panel } from "@/components/ui/Panel";
import { StatusBadge } from "@/components/ui/StatusBadge";

/** A stable workflow URL survives the baseline-to-campaign handoff and browser refresh. */
export function EvolutionLoopDetailClient({ loopId }: { loopId: string }) {
  const t = useTranslations("evolution.loop");
  const { data, error, isLoading, isValidating, mutate } = useSWR<EvolutionLoopDetailPayload>(
    `/api/evolution/loops/${loopId}`, jsonFetcher,
    { refreshInterval: (value) => loopRefreshInterval(value?.loop.status) },
  );
  const loop = data?.loop;
  if (isLoading && !loop) return <SkeletonBlock className="h-96" />;
  if (!loop) return <ErrorState message={error instanceof Error ? error.message : t("unavailable")} onRetry={() => mutate()} />;
  const stage = loopStage(loop.status);
  return (
    <div className="flex flex-col gap-6">
      <Link href="/evolution" className="text-sm text-cyan hover:underline">← {t("back")}</Link>
      <PageHeader title={`${t("title")} ${loop.loop_id.slice(0, 8)}`} subtitle={t("safety")}
        right={<LiveStrip asOf={data?.asOf ?? loop.updated_at} isValidating={isValidating} isStaleFrame={Boolean(error)} />} />
      {error && <ErrorState message={t("refreshFailed")} onRetry={() => mutate()} />}
      <Panel title={t("status")} aside={<StatusBadge label={t(`states.${loop.status}`)} tone={loop.failure_code ? "fox" : "cyan"} />}>
        <ol className="grid gap-3 p-4 md:grid-cols-5" aria-label={t("status")}>
          {["baseline", "campaign", "forward", "holdout", "adoption"].map((key, index) => (
            <li key={key} aria-current={stage === index ? "step" : undefined}
              className={`rounded-lg border p-3 text-sm ${stage === index ? "border-cyan/50 bg-cyan/5 text-cyan" : "border-border-subtle text-fg-muted"}`}>
              {index + 1}. {t(key)}
            </li>
          ))}
        </ol>
        <p className="px-4 pb-4 text-sm text-fg-muted">{t(`guidance.${loop.status}`)}</p>
      </Panel>
      <Panel title={t("costTitle")}>
        {loop.budget_usage ? <dl className="grid gap-4 p-4 text-sm sm:grid-cols-2">
          {(["max_cost_usd", "spent_usd", "reserved_usd", "available_usd"] as const).map((key) => (
            <div key={key}><dt className="text-fg-muted">{t(`cost.${key}`)}</dt>
              <dd className="font-mono">${loop.budget_usage![key].toFixed(4)}</dd></div>
          ))}
        </dl> : <p className="p-4 text-sm text-fg-muted">{t("costUnavailable")}</p>}
        <p className="px-4 pb-4 text-sm text-fg-muted">{t("costExplanation")}</p>
      </Panel>
      <Panel title={t("target")}>
        <dl className="grid gap-4 p-4 text-sm sm:grid-cols-2">
          <div><dt className="text-fg-muted">{t("target")}</dt><dd className="break-all font-mono">{loop.target_kind}: {loop.target_id}</dd></div>
          <div><dt className="text-fg-muted">{t("holdout")}</dt><dd>{loop.holdout_attempt_id ? t("consumed") : t("sealed")}</dd></div>
        </dl>
        <div className="flex flex-wrap gap-4 border-t border-border-subtle p-4 text-sm text-cyan">
          {loop.e1_run_id && <Link href={`/evolution/${loop.e1_run_id}`} className="hover:underline">{t("baseline")} →</Link>}
          {loop.campaign_id && <Link href={`/evolution/campaigns/${loop.campaign_id}`} className="hover:underline">{t("campaign")} →</Link>}
        </div>
      </Panel>
      {(loop.failure_code || loop.failure_message) && <Panel title={t("diagnostic")}>
        <p className="break-words p-4 text-sm text-fox-red">{loop.failure_code}: {loop.failure_message}</p>
      </Panel>}
    </div>
  );
}
