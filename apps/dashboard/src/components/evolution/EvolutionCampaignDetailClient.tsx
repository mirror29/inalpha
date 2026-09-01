"use client";

import { useMemo, useState } from "react";
import { useLocale, useTranslations } from "next-intl";
import { toast } from "sonner";
import useSWR from "swr";

import { Link } from "@/i18n/navigation";
import { jsonFetcher } from "@/lib/fetcher";
import { fmtRelative } from "@/lib/format";
import type { EvolutionCampaignDetailPayload, EvolutionImplementation } from "@/lib/types";
import { ErrorState, SkeletonBlock } from "@/components/ui/Feedback";
import { LiveStrip } from "@/components/ui/LiveStrip";
import { PageHeader } from "@/components/ui/PageHeader";
import { Panel } from "@/components/ui/Panel";
import { StatusBadge } from "@/components/ui/StatusBadge";

const ACTIVE = new Set(["draft", "replaying", "candidate_locked", "waiting_forward", "holdout_ready"]);

/** Campaign workbench: lineage, generations, ablations, Forward and one-shot holdout. */
export function EvolutionCampaignDetailClient({ campaignId }: { campaignId: string }) {
  const t = useTranslations("evolution.campaign");
  const locale = useLocale();
  const [adopting, setAdopting] = useState(false);
  const { data, error, isLoading, isValidating, mutate } = useSWR<EvolutionCampaignDetailPayload>(
    `/api/evolution/campaigns/${campaignId}`,
    jsonFetcher,
    { refreshInterval: (value) => value && ACTIVE.has(value.campaign.status) ? 4_000 : 0, keepPreviousData: true },
  );
  const campaign = data?.campaign;
  const implementations = useMemo(
    () => [...(campaign?.implementations ?? [])].sort((a, b) => b.generation - a.generation || (b.fitness ?? -Infinity) - (a.fitness ?? -Infinity)),
    [campaign?.implementations],
  );
  if (isLoading && !campaign) return <SkeletonBlock className="h-[36rem]" />;
  if (error && !campaign) return <ErrorState message={error instanceof Error ? error.message : String(error)} onRetry={() => mutate()} />;
  if (!campaign) return null;

  async function adopt() {
    setAdopting(true);
    try {
      const response = await fetch(`/api/evolution/campaigns/${campaignId}`, { method: "POST" });
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
      <div><Link href="/evolution" className="font-mono text-xs text-cyan hover:underline">← {t("back")}</Link></div>
      <PageHeader title={`${t("detailTitle")} ${campaign.campaign_id.slice(0, 8)}`} subtitle={t("detailSubtitle")} right={<LiveStrip asOf={data?.asOf ?? campaign.updated_at} isValidating={isValidating} isStaleFrame={Boolean(error)} />} />

      <Panel title={t("track")} aside={<StatusBadge label={campaign.status} tone={campaign.status === "graduated" ? "bull" : campaign.failure_code ? "fox" : "cyan"} />}>
        <div className="grid gap-3 p-4 md:grid-cols-5">
          {Array.from({ length: campaign.max_generations }, (_, index) => index + 1).map((generation) => {
            const item = campaign.generations.find((value) => value.generation === generation);
            return <div key={generation} className={`rounded-lg border p-3 ${generation === campaign.active_generation ? "border-cyan/50 bg-cyan/5" : "border-border-subtle"}`}><div className="font-mono text-xs text-fg-muted">G{generation}</div><div className="mt-2 text-xl text-fg">{item?.hypothesis_count ?? 0} × 3</div><div className="mt-1 font-mono text-[10px] text-fg-muted">novelty {formatMetric(item?.best_novelty)}</div></div>;
          })}
        </div>
      </Panel>

      <div className="grid gap-6 lg:grid-cols-2">
        <Panel title={t("frozenData")}><dl className="grid grid-cols-2 gap-3 p-4 text-sm"><Metric label={t("snapshot")} value={campaign.event_snapshot_id.slice(0, 8)} /><Metric label={t("facts")} value={String(snapshot?.fact_count ?? "—")} /><Metric label={t("eventHash")} value={String(snapshot?.events_sha256 ?? "—").slice(0, 12)} /><Metric label={t("executionModel")} value={String(campaign.frozen_config.execution_model_version ?? "—")} /></dl></Panel>
        <Panel title={t("forwardHoldout")}><dl className="grid grid-cols-2 gap-3 p-4 text-sm"><Metric label={t("forwardEvents")} value={String(campaign.forward_event_count)} /><Metric label={t("deadline")} value={campaign.forward_deadline_at ? fmtRelative(campaign.forward_deadline_at, Date.now(), locale) : "—"} /><Metric label={t("holdout")} value={campaign.holdout_consumed_at ? t("consumed") : t("sealed")} /><Metric label={t("winner")} value={campaign.locked_candidate_id?.slice(0, 8) ?? "—"} /></dl>{campaign.status === "graduated" && <div className="border-t border-border-subtle p-4"><button type="button" disabled={adopting} onClick={() => void adopt()} className="rounded-md border border-bull/40 bg-bull/10 px-3 py-2 font-mono text-xs text-bull disabled:opacity-50">{adopting ? t("adopting") : t("adopt")}</button><p className="mt-2 text-xs text-fg-muted">{t("adoptHint")}</p></div>}</Panel>
      </div>

      <Panel title={t("hypotheses")}>
        <div className="grid gap-3 p-4 lg:grid-cols-2">
          {campaign.hypotheses.map((hypothesis) => <article key={hypothesis.hypothesis_id} className="rounded-lg border border-border-subtle p-3"><div className="flex items-center justify-between gap-2"><div className="font-mono text-xs text-cyan">G{hypothesis.generation} · {hypothesis.lane} · {hypothesis.lineage_kind}</div>{hypothesis.selected && <StatusBadge label={t("selected")} tone="gold" />}</div><p className="mt-2 text-sm leading-6 text-fg">{String(hypothesis.spec.thesis ?? "—")}</p><div className="mt-2 flex flex-wrap gap-1">{arrayValue(hypothesis.spec.event_types).map((value) => <span key={value} className="rounded border border-border-subtle px-1.5 py-0.5 font-mono text-[10px] text-fg-muted">{value}</span>)}</div><div className="mt-2 font-mono text-[10px] text-fg-muted">credit {formatMetric(hypothesis.upper_credit)} · novelty {formatMetric(hypothesis.novelty_score)} · parents {hypothesis.parent_ids.map((value) => value.slice(0, 6)).join(", ") || "root"}</div></article>)}
        </div>
      </Panel>

      <Panel title={t("ablations")}>
        <div className="overflow-x-auto"><table className="w-full border-collapse text-sm"><thead><tr className="text-left font-mono text-[10px] uppercase tracking-wider text-fg-muted"><th className="p-3">G</th><th className="p-3">profile</th><th className="p-3">outcome</th><th className="p-3 text-right">fitness</th><th className="p-3 text-right">event advantage</th><th className="p-3 text-right">evidence</th><th className="p-3">FDR</th></tr></thead><tbody>{implementations.map((item) => <ImplementationRow key={item.implementation_id} item={item} />)}</tbody></table></div>
      </Panel>

      {(campaign.failure_code || campaign.failure_message) && <Panel title={t("failure")}><div className="p-4 font-mono text-sm text-fox-red">{campaign.failure_code}: {campaign.failure_message}</div></Panel>}
    </div>
  );
}

/** Render one lower-level implementation without exposing source code in the aggregate view. */
function ImplementationRow({ item }: { item: EvolutionImplementation }) {
  const eventMetrics = objectValue(item.event_metrics);
  return <tr className="border-t border-border-subtle/60"><td className="p-3 font-mono text-fg-muted">{item.generation}</td><td className="p-3 font-mono text-cyan">{item.profile}</td><td className="p-3"><StatusBadge label={item.outcome} tone={item.outcome === "succeeded" ? "bull" : item.outcome === "failed" ? "fox" : "muted"} /></td><td className="p-3 text-right font-mono">{formatMetric(item.fitness)}</td><td className="p-3 text-right font-mono">{formatMetric(numberValue(eventMetrics?.event_advantage_pct))}</td><td className="p-3 text-right font-mono">{formatMetric(item.evidence_quality)}</td><td className="p-3 font-mono text-xs text-fg-muted">{item.fdr_pass === null ? "—" : item.fdr_pass ? "pass" : "fail"}</td></tr>;
}

function Metric({ label, value }: { label: string; value: string }) { return <div><dt className="font-mono text-[10px] uppercase tracking-wider text-fg-muted">{label}</dt><dd className="mt-1 break-all font-mono text-sm text-fg">{value}</dd></div>; }
function objectValue(value: unknown): Record<string, unknown> | null { return typeof value === "object" && value !== null && !Array.isArray(value) ? value as Record<string, unknown> : null; }
function arrayValue(value: unknown): string[] { return Array.isArray(value) ? value.map(String) : []; }
function numberValue(value: unknown): number | null { return typeof value === "number" ? value : null; }
function formatMetric(value: number | null | undefined): string { return value === null || value === undefined ? "—" : value.toFixed(3); }
