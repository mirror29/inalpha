"use client";

import { useMemo, useState } from "react";
import { useTranslations } from "next-intl";
import { Radio } from "lucide-react";
import useSWR from "swr";

import type { RunnersPayload } from "@/lib/types";
import { cn } from "@/lib/cn";
import { jsonFetcher } from "@/lib/fetcher";
import { ErrorState, SkeletonBlock } from "@/components/ui/Feedback";
import { LiveStrip, Meta } from "@/components/ui/LiveStrip";
import { PageHeader } from "@/components/ui/PageHeader";
import { TruncationNote } from "@/components/ui/TruncationNote";
import { RunnerCard } from "./RunnerCard";

/** live runner 贴近 bar 周期,6s 一档。 */
const REFRESH_MS = 6000;

type StatusFilter = "all" | "running" | "stopped" | "errored";
const STATUSES: StatusFilter[] = ["all", "running", "stopped", "errored"];

export function RunnersClient() {
  const t = useTranslations("runners");
  const tc = useTranslations("common");
  const [filter, setFilter] = useState<StatusFilter>("all");

  const { data, error, isValidating, isLoading, mutate } =
    useSWR<RunnersPayload>("/api/runners", jsonFetcher, {
      refreshInterval: REFRESH_MS,
      keepPreviousData: true,
    });

  // 各状态计数(chip 角标)。
  const counts = useMemo(() => {
    const c = { all: 0, running: 0, stopped: 0, errored: 0 };
    if (data) {
      c.all = data.runs.length;
      for (const r of data.runs) c[r.status] += 1;
    }
    return c;
  }, [data]);

  const filtered = useMemo(
    () =>
      data
        ? filter === "all"
          ? data.runs
          : data.runs.filter((r) => r.status === filter)
        : [],
    [data, filter],
  );

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

      {/* 状态筛选 chip */}
      <div className="flex flex-wrap gap-1.5">
        {STATUSES.map((s) => (
          <FilterChip
            key={s}
            label={`${t(`filter.${s}`)} ${counts[s]}`}
            active={filter === s}
            onClick={() => setFilter(s)}
          />
        ))}
      </div>

      {filtered.length === 0 ? (
        <div className="flex flex-col items-center justify-center gap-3 rounded-xl border border-dashed border-border-subtle py-20 text-center">
          <Radio className="size-7 text-fg-muted/50" strokeWidth={1.5} />
          <p className="max-w-md text-sm text-fg-muted">
            {data.runs.length === 0
              ? t("empty")
              : t("emptyFiltered", { status: t(`filter.${filter}`) })}
          </p>
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {filtered.map((run) => (
            <RunnerCard key={run.id} run={run} />
          ))}
        </div>
      )}

      {data.truncated && (
        <TruncationNote text={tc("truncated", { n: data.runs.length })} />
      )}
    </div>
  );
}

function FilterChip({
  label,
  active,
  onClick,
}: {
  label: string;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "rounded-md border px-2.5 py-1 font-mono text-[10px] uppercase tracking-wider transition-colors",
        active
          ? "border-cyan/40 bg-cyan/10 text-cyan"
          : "border-border-subtle text-fg-muted hover:text-fg",
      )}
    >
      {label}
    </button>
  );
}
