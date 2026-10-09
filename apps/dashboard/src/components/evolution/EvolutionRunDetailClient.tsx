"use client";

import { useState } from "react";
import { useTranslations } from "next-intl";
import { ArrowLeft } from "lucide-react";
import useSWR from "swr";

import { Link } from "@/i18n/navigation";
import type { EvolutionRunDetailPayload } from "@/lib/types";
import {
  evolutionRefreshInterval,
} from "@/lib/evolution";
import { jsonFetcher } from "@/lib/fetcher";
import { ErrorState, SkeletonBlock } from "@/components/ui/Feedback";
import { Panel } from "@/components/ui/Panel";
import { EvolutionAbortDialog } from "./EvolutionAbortDialog";
import { EvolutionComparison } from "./EvolutionComparison";
import { EvolutionCandidates } from "./EvolutionCandidates";
import { EvolutionRunData } from "./EvolutionRunData";
import { EvolutionRunHeader } from "./EvolutionRunHeader";

/** 单次 E1 演化运行详情、条件轮询与中止操作。 */
export function EvolutionRunDetailClient({ runId }: { runId: string }) {
  const t = useTranslations("evolution.detail");
  const [confirmAbort, setConfirmAbort] = useState(false);
  const [aborting, setAborting] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const { data, error, isLoading, isValidating, mutate } =
    useSWR<EvolutionRunDetailPayload>(`/api/evolution/${runId}`, jsonFetcher, {
      refreshInterval: (latest) => evolutionRefreshInterval(latest?.run.status),
      keepPreviousData: true,
    });

  const abort = async () => {
    setAborting(true);
    setActionError(null);
    try {
      const response = await fetch(`/api/evolution/${runId}`, { method: "POST" });
      const body = (await response.json()) as EvolutionRunDetailPayload & { error?: string };
      if (!response.ok) throw new Error(body.error ?? `HTTP ${response.status}`);
      await mutate(body, { revalidate: false });
      setConfirmAbort(false);
    } catch (abortError) {
      setActionError(abortError instanceof Error ? abortError.message : String(abortError));
    } finally {
      setAborting(false);
    }
  };

  if (isLoading && !data) return <EvolutionDetailSkeleton />;
  if (error && !data) {
    return <ErrorState message={String(error)} onRetry={() => mutate()} />;
  }
  if (!data) return null;
  const { run } = data;

  return (
    <div className="flex flex-col gap-6">
      <Link href="/evolution" className="inline-flex w-fit items-center gap-1.5 font-mono text-xs text-fg-muted hover:text-cyan">
        <ArrowLeft className="size-3.5" />
        {t("back")}
      </Link>
      <EvolutionRunHeader
        run={run}
        asOf={data.asOf}
        isValidating={isValidating}
        isStaleFrame={Boolean(error)}
        onAbort={() => setConfirmAbort(true)}
      />
      {actionError && <p role="alert" className="rounded-lg border border-fox-red/30 bg-fox-red/10 px-3 py-2 text-sm text-fox-red">{actionError}</p>}
      <EvolutionComparison run={run} />
      <EvolutionRunData run={run} />
      <Panel title={t("candidates")} aside={<span className="font-mono text-xs text-fg-muted">{run.attempted}/{run.budget}</span>}>
        <EvolutionCandidates candidates={run.candidates} />
      </Panel>
      <EvolutionAbortDialog
        open={confirmAbort}
        busy={aborting}
        onOpenChange={setConfirmAbort}
        onConfirm={() => void abort()}
      />
    </div>
  );
}

function EvolutionDetailSkeleton() {
  return <div className="flex flex-col gap-6"><SkeletonBlock className="h-16 border-0 bg-bg-elev/40" /><SkeletonBlock className="h-72" /></div>;
}
