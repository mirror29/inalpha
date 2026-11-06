"use client";

import { EvolutionHypothesisLineage } from "./EvolutionHypothesisLineage";
import { EvolutionLineage } from "./EvolutionLineage";

import { EvolutionChatCosts } from "./EvolutionChatCosts";

import { useEffect, useMemo, useState } from "react";
import { useLocale, useTranslations } from "next-intl";
import { toast } from "sonner";
import useSWR from "swr";

import { Link } from "@/i18n/navigation";
import { evolutionMarket, workflowState } from "@/lib/evolution-presentation";
import { jsonFetcher } from "@/lib/fetcher";
import { fmtRelative } from "@/lib/format";
import type {
  EvolutionCampaignDetailPayload,
  EvolutionHypothesis,
  EvolutionImplementation,
  EvolutionImplementationPage,
} from "@/lib/types";
import { ErrorState, SkeletonBlock } from "@/components/ui/Feedback";
import { LiveStrip } from "@/components/ui/LiveStrip";
import { PageHeader } from "@/components/ui/PageHeader";
import { Panel } from "@/components/ui/Panel";
import { StatusBadge } from "@/components/ui/StatusBadge";

const ACTIVE = new Set([
  "draft",
  "replaying",
  "candidate_locked",
  "waiting_forward",
  "holdout_ready",
]);

/** Campaign workbench: lineage, generations, ablations, Forward and one-shot holdout. */
export function EvolutionCampaignDetailClient({
  campaignId,
}: {
  campaignId: string;
}) {
  const t = useTranslations("evolution.campaign");
  const locale = useLocale();
  const w = useTranslations("evolution.workflow");
  const l = useTranslations("evolution.loop");
  const [selectedGeneration, setSelectedGeneration] = useState<number | null>(
    null,
  );
  const [adopting, setAdopting] = useState(false);
  const [focusedHypothesis, setFocusedHypothesis] = useState<string | null>(
    null,
  );
  const [implementationOffset, setImplementationOffset] = useState(0);
  const [loadedImplementations, setLoadedImplementations] = useState<
    EvolutionImplementation[]
  >([]);
  const { data, error, isLoading, isValidating, mutate } =
    useSWR<EvolutionCampaignDetailPayload>(
      `/api/evolution/campaigns/${campaignId}`,
      jsonFetcher,
      {
        refreshInterval: (value) =>
          value && ACTIVE.has(value.campaign.status) ? 4_000 : 0,
        keepPreviousData: true,
      },
    );
  const campaign = data?.campaign;
  const shownGeneration =
    selectedGeneration ?? campaign?.active_generation ?? 1;
  useEffect(() => {
    if (focusedHypothesis)
      document
        .getElementById(`hypothesis-${focusedHypothesis}`)
        ?.scrollIntoView({ block: "center" });
  }, [focusedHypothesis, shownGeneration]);
  function inspectHypothesis(hypothesis: EvolutionHypothesis) {
    setFocusedHypothesis(hypothesis.hypothesis_id);
    setSelectedGeneration(hypothesis.generation);
    setImplementationOffset(0);
  }
  const implementationKey = !campaign
    ? null
    : `/api/evolution/campaigns/${campaignId}/implementations?generation=${shownGeneration}&limit=24&offset=${implementationOffset}`;
  const {
    data: implementationPage,
    error: implementationsError,
    mutate: retryImplementations,
    isLoading: implementationsLoading,
    isValidating: implementationsValidating,
  } = useSWR<EvolutionImplementationPage>(implementationKey, jsonFetcher, {
    refreshInterval: campaign && ACTIVE.has(campaign.status) ? 4000 : 0,
    onSuccess: (page) =>
      setLoadedImplementations((current) =>
        implementationOffset === 0
          ? page.items
          : mergeImplementations(current, page.items),
      ),
  });
  useEffect(() => {
    if (campaign?.status) void retryImplementations();
  }, [campaign?.status, campaign?.active_generation, retryImplementations]);
  const implementations = useMemo(
    () =>
      mergeImplementations(
        loadedImplementations,
        implementationPage?.items ?? [],
      ).sort(
        (a, b) =>
          b.generation - a.generation ||
          (b.fitness ?? -Infinity) - (a.fitness ?? -Infinity),
      ),
    [loadedImplementations, implementationPage],
  );
  if (isLoading && !campaign) return <SkeletonBlock className="h-[36rem]" />;
  if (error && !campaign)
    return (
      <ErrorState
        message={error instanceof Error ? error.message : String(error)}
        onRetry={() => mutate()}
      />
    );
  if (!campaign) return null;

  async function adopt() {
    setAdopting(true);
    try {
      const response = await fetch(`/api/evolution/campaigns/${campaignId}`, {
        method: "POST",
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      toast.success(t("adopted"));
      await mutate();
    } catch (cause) {
      toast.error(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setAdopting(false);
    }
  }

  const snapshot = objectValue(campaign.frozen_config.event_snapshot);
  return (
    <div className="flex flex-col gap-6">
      <div>
        <Link
          href="/evolution"
          className="font-mono text-xs text-cyan hover:underline"
        >
          ← {t("back")}
        </Link>
      </div>
      <PageHeader
        title={w("detailTitle")}
        subtitle={
          evolutionMarket(campaign.frozen_config) || w("marketUnavailable")
        }
        right={
          <LiveStrip
            asOf={data?.asOf ?? campaign.updated_at}
            isValidating={isValidating}
            isStaleFrame={Boolean(error)}
          />
        }
      />

      {error && (
        <ErrorState message={l("refreshFailed")} onRetry={() => mutate()} />
      )}
      <Panel title={w("conclusion")}>
        <div className="p-5">
          <h2 className="text-xl">
            {l(`states.${workflowState(campaign.status)}`)}
          </h2>
          <p className="mt-2 text-sm leading-7 text-fg-muted">
            {l(`guidance.${workflowState(campaign.status)}`)}
          </p>
        </div>
      </Panel>
      <EvolutionLineage lineage={campaign.lineage} />
      <EvolutionChatCosts costs={campaign.chat_preparation_costs} />
      <Panel
        title={w("rounds")}
        aside={
          <StatusBadge
            label={l(`states.${workflowState(campaign.status)}`)}
            tone={
              campaign.status === "graduated"
                ? "bull"
                : campaign.failure_code
                  ? "fox"
                  : "cyan"
            }
          />
        }
      >
        <div className="grid gap-3 p-4 md:grid-cols-5">
          {Array.from(
            { length: campaign.max_generations },
            (_, index) => index + 1,
          ).map((generation) => {
            const item = campaign.generations.find(
              (value) => value.generation === generation,
            );
            return (
              <button
                type="button"
                key={generation}
                aria-pressed={shownGeneration === generation}
                onClick={() => {
                  setSelectedGeneration(generation);
                  setImplementationOffset(0);
                }}
                className={`rounded-lg border p-3 text-left ${generation === shownGeneration ? "border-cyan/50 bg-cyan/5" : "border-border-subtle"}`}
              >
                <div className="text-sm text-fg">
                  {w("round", { n: generation })}
                </div>
                <div className="mt-2 text-xs text-fg-muted">
                  {w("hypothesisCount", { count: item?.hypothesis_count ?? 0 })}
                </div>
              </button>
            );
          })}
        </div>
      </Panel>

      <Panel title={w("steps.forward")}>
        <div className="p-4">
          <p className="text-sm leading-7 text-fg-muted">{w("forwardHint")}</p>
          <p className="mt-3 text-sm">
            {campaign.locked_candidate_id
              ? w("candidateLocked")
              : w("notStarted")}
          </p>
          {campaign.forward_started_at && (
            <dl className="mt-3 grid grid-cols-2 gap-3">
              <Metric
                label={t("forwardEvents")}
                value={String(campaign.forward_event_count)}
              />
              <Metric
                label={t("deadline")}
                value={
                  campaign.forward_deadline_at
                    ? fmtRelative(
                        campaign.forward_deadline_at,
                        Date.now(),
                        locale,
                      )
                    : "—"
                }
              />
            </dl>
          )}
          {campaign.status === "graduated" && (
            <div className="mt-4">
              <button
                type="button"
                disabled={adopting}
                onClick={() => void adopt()}
                className="rounded-md border border-bull/40 bg-bull/10 px-3 py-2 text-sm text-bull disabled:opacity-50"
              >
                {adopting ? t("adopting") : t("adopt")}
              </button>
              <p className="mt-2 text-xs text-fg-muted">{l("safety")}</p>
            </div>
          )}
        </div>
      </Panel>

      <Panel title={w("hypotheses")}>
        <p className="px-4 pt-4 text-sm leading-6 text-fg-muted">
          {w("hypothesisHint")}
        </p>
        <div className="grid gap-3 p-4 lg:grid-cols-2">
          {campaign.hypotheses
            .filter((hypothesis) => hypothesis.generation === shownGeneration)
            .map((hypothesis) => (
              <article
                key={hypothesis.hypothesis_id}
                id={`hypothesis-${hypothesis.hypothesis_id}`}
                className={`rounded-lg border p-3 ${focusedHypothesis === hypothesis.hypothesis_id ? "border-cyan/50 bg-cyan/5" : "border-border-subtle"}`}
              >
                <div className="flex items-center justify-between gap-2">
                  <div className="font-mono text-xs text-cyan">
                    {w("round", { n: hypothesis.generation })}
                  </div>
                  {hypothesis.selected && (
                    <StatusBadge label={w("selected")} tone="gold" />
                  )}
                </div>
                <p className="mt-2 text-sm leading-6 text-fg">
                  {String(hypothesis.spec.thesis ?? "—")}
                </p>
                <div className="mt-2 flex flex-wrap gap-1">
                  {arrayValue(hypothesis.spec.event_types).map((value) => (
                    <span
                      key={value}
                      className="rounded border border-border-subtle px-1.5 py-0.5 font-mono text-[10px] text-fg-muted"
                    >
                      {w.has(`eventTypes.${value}`)
                        ? w(`eventTypes.${value}`)
                        : value}
                    </span>
                  ))}
                </div>
                <EvolutionHypothesisLineage
                  hypothesis={hypothesis}
                  hypotheses={campaign.hypotheses}
                  onInspect={inspectHypothesis}
                />
                <div className="mt-2 font-mono text-[10px] text-fg-muted">
                  {w("credit")} {formatMetric(hypothesis.upper_credit)} ·{" "}
                  {w("novelty")} {formatMetric(hypothesis.novelty_score)}
                </div>
              </article>
            ))}
        </div>
      </Panel>

      <Panel title={w("comparisons")}>
        <p className="px-4 pt-4 text-sm leading-6 text-fg-muted">
          {w("fdrHint")}
        </p>
        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-sm">
            <thead>
              <tr className="text-left font-mono text-[10px] uppercase tracking-wider text-fg-muted">
                <th className="p-3">{w("round", { n: shownGeneration })}</th>
                <th className="p-3">{w("profile")}</th>
                <th className="p-3">{w("outcome")}</th>
                <th className="p-3 text-right">{w("fitness")}</th>
                <th className="p-3 text-right">{w("advantage")}</th>
                <th className="p-3 text-right">{w("evidenceQuality")}</th>
                <th className="p-3">{w("statisticalCheck")}</th>
              </tr>
            </thead>
            <tbody>
              {implementations
                .filter((item) => item.generation === shownGeneration)
                .map((item) => (
                  <ImplementationRow
                    key={item.implementation_id}
                    item={item}
                    hypothesis={campaign.hypotheses.find(
                      (value) =>
                        value.hypothesis_id === item.hypothesis_id &&
                        value.campaign_id === item.campaign_id &&
                        value.generation === item.generation,
                    )}
                    onInspect={inspectHypothesis}
                  />
                ))}
            </tbody>
          </table>
        </div>
        {!implementationsLoading &&
          !implementationsError &&
          !implementations.some(
            (item) => item.generation === shownGeneration,
          ) && (
            <p className="p-4 text-sm text-fg-muted">
              {w(
                implementationPage?.has_more ? "roundNotLoaded" : "roundEmpty",
              )}
            </p>
          )}
        {implementationsError && (
          <ErrorState
            message={l("refreshFailed")}
            onRetry={() => retryImplementations()}
          />
        )}
        {(implementationPage?.has_more || implementationsLoading) && (
          <div className="border-t border-border-subtle p-3 text-center">
            <button
              type="button"
              disabled={implementationsLoading || implementationsValidating}
              onClick={() => setImplementationOffset((value) => value + 24)}
              className="rounded-md border border-border-subtle px-3 py-1.5 font-mono text-xs text-fg-muted hover:text-cyan disabled:opacity-50"
            >
              {implementationsLoading || implementationsValidating
                ? t("loadingMore")
                : t("loadMore")}
            </button>
          </div>
        )}
      </Panel>

      <details className="rounded-xl border border-border-subtle">
        <summary className="cursor-pointer p-4 text-sm text-fg-muted">
          {w("audit")}
        </summary>
        <div className="space-y-4 p-4 pt-0">
          <p className="text-sm text-fg-muted">{w("auditHint")}</p>
          <Panel title={t("frozenData")}>
            <p className="px-4 pt-4 text-sm text-fg-muted">
              {w("dataExplanation")}
            </p>
            <dl className="grid gap-3 p-4 sm:grid-cols-2">
              <Metric label={w("recordId")} value={campaign.campaign_id} />
              <Metric
                label={t("snapshot")}
                value={campaign.event_snapshot_id}
              />
              <Metric
                label={t("facts")}
                value={String(snapshot?.fact_count ?? "—")}
              />
              <Metric
                label={t("eventHash")}
                value={String(snapshot?.events_sha256 ?? "—")}
              />
              <Metric
                label={t("executionModel")}
                value={String(
                  campaign.frozen_config.execution_model_version ?? "—",
                )}
              />
              <Metric
                label={t("holdout")}
                value={
                  campaign.holdout_consumed_at ? t("consumed") : t("sealed")
                }
              />
            </dl>
          </Panel>
          {(campaign.failure_code || campaign.failure_message) && (
            <Panel title={t("failure")}>
              <div className="p-4 font-mono text-sm text-fox-red">
                {campaign.failure_code}: {campaign.failure_message}
              </div>
            </Panel>
          )}
        </div>
      </details>
    </div>
  );
}

/** Render one lower-level implementation without exposing source code in the aggregate view. */
function ImplementationRow({
  item,
  hypothesis,
  onInspect,
}: {
  item: EvolutionImplementation;
  hypothesis?: EvolutionHypothesis;
  onInspect: (hypothesis: EvolutionHypothesis) => void;
}) {
  const t = useTranslations("evolution.lineage");
  const w = useTranslations("evolution.workflow");
  const eventMetrics = objectValue(item.event_metrics);
  return (
    <tr className="border-t border-border-subtle/60">
      <td className="p-3 font-mono text-fg-muted">{item.generation}</td>
      <td className="p-3 font-mono text-cyan">
        {w.has(`profiles.${item.profile}`)
          ? w(`profiles.${item.profile}`)
          : item.profile}
        {hypothesis && (
          <button
            type="button"
            onClick={() => onInspect(hypothesis)}
            className="mt-1 block text-xs hover:underline"
          >
            {t("hypothesis", { number: hypothesis.slot + 1 })} →
          </button>
        )}
      </td>
      <td className="p-3">
        <StatusBadge
          label={
            w.has(`outcomes.${item.outcome}`)
              ? w(`outcomes.${item.outcome}`)
              : item.outcome
          }
          tone={
            item.outcome === "succeeded"
              ? "bull"
              : item.outcome === "failed"
                ? "fox"
                : "muted"
          }
        />
      </td>
      <td className="p-3 text-right font-mono">{formatMetric(item.fitness)}</td>
      <td className="p-3 text-right font-mono">
        {formatMetric(numberValue(eventMetrics?.event_advantage_pct))}
      </td>
      <td className="p-3 text-right font-mono">
        {formatMetric(item.evidence_quality)}
      </td>
      <td className="p-3 font-mono text-xs text-fg-muted">
        {item.fdr_pass === null ? "—" : item.fdr_pass ? w("pass") : w("noPass")}
      </td>
    </tr>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="font-mono text-[10px] uppercase tracking-wider text-fg-muted">
        {label}
      </dt>
      <dd className="mt-1 break-all font-mono text-sm text-fg">{value}</dd>
    </div>
  );
}
function objectValue(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}
function arrayValue(value: unknown): string[] {
  return Array.isArray(value) ? value.map(String) : [];
}
function numberValue(value: unknown): number | null {
  return typeof value === "number" ? value : null;
}
function formatMetric(value: number | null | undefined): string {
  return value === null || value === undefined ? "—" : value.toFixed(3);
}
function mergeImplementations(
  current: EvolutionImplementation[],
  next: EvolutionImplementation[],
): EvolutionImplementation[] {
  const byId = new Map(current.map((item) => [item.implementation_id, item]));
  next.forEach((item) => byId.set(item.implementation_id, item));
  return [...byId.values()];
}
