"use client";

import { useTranslations } from "next-intl";
import { ArrowLeft } from "lucide-react";
import useSWR from "swr";

import { Link } from "@/i18n/navigation";
import type { EvolutionCandidateDetailPayload } from "@/lib/types";
import { jsonFetcher } from "@/lib/fetcher";
import { candidateCost, evolutionUsd } from "@/lib/evolution-cost";
import { CodeViewer } from "@/components/ui/CodeViewer";
import { ErrorState, SkeletonBlock } from "@/components/ui/Feedback";
import { LiveStrip } from "@/components/ui/LiveStrip";
import { Panel } from "@/components/ui/Panel";
import { StatusBadge } from "@/components/ui/StatusBadge";

/** 单个演化 slot 的源码、diff、评估和拒绝原因。 */
export function EvolutionCandidateDetailClient({
  candidateId,
}: {
  candidateId: string;
}) {
  const t = useTranslations("evolution.candidate");
  const w = useTranslations("evolution.workflow");
  const c = useTranslations("evolution.costs");
  const { data, error, isLoading, isValidating, mutate } =
    useSWR<EvolutionCandidateDetailPayload>(
      `/api/evolution/candidates/${candidateId}`,
      jsonFetcher,
      {
        revalidateOnFocus: false,
      },
    );
  if (isLoading && !data)
    return (
      <div className="flex flex-col gap-6">
        <SkeletonBlock className="h-16 border-0 bg-bg-elev/40" />
        <SkeletonBlock className="h-72" />
      </div>
    );
  if (error && !data)
    return <ErrorState message={String(error)} onRetry={() => mutate()} />;
  if (!data) return null;
  const candidate = data.candidate;
  const cost = candidateCost(candidate);
  return (
    <div className="flex flex-col gap-6">
      <Link
        href={`/evolution/${candidate.run_id}`}
        className="inline-flex w-fit items-center gap-1.5 font-mono text-xs text-fg-muted hover:text-cyan"
      >
        <ArrowLeft className="size-3.5" />
        {t("back")}
      </Link>
      <header className="flex flex-col gap-4 border-b border-border-subtle pb-5 lg:flex-row lg:items-end lg:justify-between">
        <div>
          <div className="flex items-center gap-3">
            <h1 className="font-display text-3xl text-fg lg:text-4xl">
              {t("title", { slot: candidate.slot + 1 })}
            </h1>
            <StatusBadge
              label={
                w.has(`outcomes.${candidate.outcome}`)
                  ? w(`outcomes.${candidate.outcome}`)
                  : candidate.outcome
              }
              tone={candidate.outcome === "succeeded" ? "bull" : "fox"}
            />
          </div>
          <p className="mt-2 font-mono text-xs text-fg-muted">
            {candidate.mutation_hint ?? w("hypothesisHint")}
          </p>
        </div>
        <LiveStrip
          asOf={data.asOf}
          isValidating={isValidating}
          isStaleFrame={Boolean(error)}
        />
      </header>
      {candidate.parent_id && (
        <Link href={`/evolution/candidates/${candidate.parent_id}`} className="text-sm text-cyan hover:underline">
          {c("repairParentLink")}
        </Link>
      )}
      {(candidate.error_code || candidate.error_message) && (
        <Panel title={t("error")}>
          <div className="space-y-2 p-4 text-sm text-fox-red">
            <p className="font-mono">
              {candidate.error_code ?? "EVOLUTION_SLOT_FAILED"}
            </p>
            <p>{candidate.error_message ?? "—"}</p>
          </div>
        </Panel>
      )}
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat
          label={t("stage")}
          value={
            w.has(`stages.${candidate.stage}`)
              ? w(`stages.${candidate.stage}`)
              : w("stages.unknown")
          }
        />
        <Stat
          label={t("fitness")}
          value={candidate.fitness?.toFixed(4) ?? "—"}
        />
        <Stat
          label={t("risk")}
          value={
            w.has(`risks.${candidate.overfitting_risk}`)
              ? w(`risks.${candidate.overfitting_risk}`)
              : w("unknown")
          }
        />
        <Stat
          label={t("cost")}
          value={
            cost.state === "known" ? (evolutionUsd(cost.confirmed) ?? c("unknownState"))
              : c(cost.state === "notCalled" ? "notCalled" : "unknownState")
          }
        />
      </div>
      <details className="rounded-xl border border-border-subtle">
        <summary className="cursor-pointer p-4 text-sm text-fg-muted">
          {w("audit")}
        </summary>
        <div className="space-y-4 p-4 pt-0">
          <p className="text-sm text-fg-muted">{w("auditHint")}</p>
          <p className="break-all font-mono text-xs">
            {w("recordId")}: {candidate.candidate_id}
          </p>
          <p className="font-mono text-xs text-fg-muted">
            {c("inputTokens")}: {cost.state === "known" ? (candidate.input_tokens ?? "—") : "—"} · {c("outputTokens")}: {cost.state === "known" ? (candidate.output_tokens ?? "—") : "—"}
          </p>
          {candidate.unified_diff && (
            <Panel title={t("diff")}>
              <CodeViewer
                code={candidate.unified_diff}
                lang="diff"
                copyLabel={t("copy")}
                copiedLabel={t("copied")}
                className="m-4"
              />
            </Panel>
          )}
          {candidate.source_code && (
            <Panel title={t("source")}>
              <CodeViewer
                code={candidate.source_code}
                lang="python"
                copyLabel={t("copy")}
                copiedLabel={t("copied")}
                className="m-4"
              />
            </Panel>
          )}
          <Snapshot
            title={t("evaluation")}
            value={candidate.evaluation_snapshot}
            empty={t("notAvailable")}
          />
        </div>
      </details>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-xl border border-border-subtle bg-bg-elev/30 p-4">
      <p className="font-mono text-[10px] uppercase tracking-wider text-fg-muted">
        {label}
      </p>
      <p className="mt-2 break-all font-mono text-sm text-fg">{value}</p>
    </div>
  );
}
function Snapshot({
  title,
  value,
  empty,
}: {
  title: string;
  value: Record<string, unknown> | null;
  empty: string;
}) {
  return (
    <Panel title={title}>
      {value ? (
        <pre className="max-h-96 overflow-auto p-4 font-mono text-xs text-fg-muted">
          {JSON.stringify(value, null, 2)}
        </pre>
      ) : (
        <p className="p-4 text-sm text-fg-muted">{empty}</p>
      )}
    </Panel>
  );
}
