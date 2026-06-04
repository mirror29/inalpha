"use client";

import { useTranslations } from "next-intl";
import { Radio } from "lucide-react";
import useSWR from "swr";

import type { RunnersPayload } from "@/lib/types";
import { jsonFetcher } from "@/lib/fetcher";
import { ErrorState, SkeletonBlock } from "@/components/ui/Feedback";
import { LiveStrip, Meta } from "@/components/ui/LiveStrip";
import { PageHeader } from "@/components/ui/PageHeader";
import { RunnerCard } from "./RunnerCard";

/** live runner 贴近 bar 周期,6s 一档。 */
const REFRESH_MS = 6000;

export function RunnersClient() {
  const t = useTranslations("runners");

  const { data, error, isValidating, isLoading, mutate } =
    useSWR<RunnersPayload>("/api/runners", jsonFetcher, {
      refreshInterval: REFRESH_MS,
      keepPreviousData: true,
    });

  if (isLoading && !data) {
    return (
      <div className="flex flex-col gap-6">
        <SkeletonBlock className="h-16 w-72 border-0 bg-bg-elev/40" />
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {Array.from({ length: 3 }).map((_, i) => (
            <SkeletonBlock key={i} className="h-48" />
          ))}
        </div>
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

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        index={t("index")}
        title={t("title")}
        subtitle={t("subtitle")}
        right={
          <LiveStrip
            asOf={data.asOf}
            isValidating={isValidating}
            isStaleFrame={Boolean(error)}
          >
            <Meta
              label={t("running")}
              value={`${data.runningCount}/${data.runs.length}`}
              tone={data.runningCount > 0 ? "bull" : "muted"}
            />
          </LiveStrip>
        }
      />

      {data.runs.length === 0 ? (
        <div className="flex flex-col items-center justify-center gap-3 rounded-xl border border-dashed border-border-subtle py-20 text-center">
          <Radio className="size-7 text-fg-muted/50" strokeWidth={1.5} />
          <p className="max-w-md text-sm text-fg-muted">{t("empty")}</p>
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {data.runs.map((run) => (
            <RunnerCard key={run.id} run={run} />
          ))}
        </div>
      )}
    </div>
  );
}
