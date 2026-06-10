"use client";

import { useMemo, useState } from "react";
import { useTranslations } from "next-intl";
import { Info, TriangleAlert } from "lucide-react";
import useSWR from "swr";

import type { ActivityKind, ActivityPayload } from "@/lib/types";
import { cn } from "@/lib/cn";
import { jsonFetcher } from "@/lib/fetcher";
import { ErrorState, SkeletonBlock } from "@/components/ui/Feedback";
import { LiveStrip } from "@/components/ui/LiveStrip";
import { PageHeader } from "@/components/ui/PageHeader";
import { Panel } from "@/components/ui/Panel";
import { ActivityFeed } from "./ActivityFeed";

/** agent 活动 6–10s 一档,取中间 8s。 */
const REFRESH_MS = 8000;

const KINDS: ActivityKind[] = [
  "scheduler",
  "permission",
  "decision",
  "risk",
  "order",
  "backtest",
  "conversation",
];
type Filter = ActivityKind | "all";

export function ActivityClient() {
  const t = useTranslations("activity");
  const [filter, setFilter] = useState<Filter>("all");

  const { data, error, isValidating, isLoading, mutate } =
    useSWR<ActivityPayload>("/api/activity", jsonFetcher, {
      refreshInterval: REFRESH_MS,
      keepPreviousData: true,
    });

  const filtered = useMemo(
    () =>
      data
        ? filter === "all"
          ? data.events
          : data.events.filter((e) => e.kind === filter)
        : [],
    [data, filter],
  );

  if (isLoading && !data) {
    return (
      <div className="flex flex-col gap-6">
        <SkeletonBlock className="h-16 w-72 border-0 bg-bg-elev/40" />
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          {Array.from({ length: 4 }).map((_, i) => (
            <SkeletonBlock key={i} className="h-20" />
          ))}
        </div>
        <SkeletonBlock className="h-96" />
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

  const downSources = (Object.keys(data.sources) as Array<keyof typeof data.sources>)
    .filter((k) => !data.sources[k])
    .map((k) => t(`source.${k}`));

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
          />
        }
      />

      {/* KPI 条 */}
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat
          label={t("kpi.scheduler")}
          value={
            data.schedulerRunning ? t("kpi.schedulerOn") : t("kpi.schedulerOff")
          }
          tone={data.schedulerRunning ? "bull" : "muted"}
        />
        <Stat
          label={t("kpi.pending")}
          value={String(data.pendingCount)}
          tone={data.pendingCount > 0 ? "gold" : "muted"}
        />
        <Stat
          label={t("kpi.locks")}
          value={String(data.activeLockCount)}
          tone={data.activeLockCount > 0 ? "fox" : "muted"}
        />
        <Stat label={t("kpi.events")} value={String(data.events.length)} />
      </div>

      {/* 源不可用提示(不静默)*/}
      {downSources.length > 0 && (
        <div className="flex items-start gap-2.5 rounded-lg border border-gold/30 bg-gold/[0.07] px-4 py-2.5">
          <TriangleAlert className="mt-0.5 size-4 shrink-0 text-gold" strokeWidth={2} />
          <p className="text-sm text-fg-muted">
            {t("sourceDown", { sources: downSources.join(" / ") })}
          </p>
        </div>
      )}

      {/* 决策截断提示(只覆盖最近 N 个 run,更早的决策事件未纳入)*/}
      {data.decisionsTruncated && (
        <div className="flex items-start gap-2.5 rounded-lg border border-border-subtle/60 bg-bg-elev/30 px-4 py-2.5">
          <Info className="mt-0.5 size-4 shrink-0 text-fg-muted" strokeWidth={2} />
          <p className="text-sm text-fg-muted">{t("decisionsTruncated")}</p>
        </div>
      )}

      <Panel
        title={t("title")}
        aside={
          <div className="flex flex-wrap gap-1">
            <FilterChip
              label={t("filter.all")}
              active={filter === "all"}
              onClick={() => setFilter("all")}
            />
            {KINDS.map((k) => (
              <FilterChip
                key={k}
                label={`${t(`filter.${k}`)} ${data.counts[k]}`}
                active={filter === k}
                onClick={() => setFilter(k)}
              />
            ))}
          </div>
        }
      >
        {filtered.length === 0 ? (
          <div className="px-4 py-12 text-center text-sm text-fg-muted/70">
            {filter === "all"
              ? t("empty")
              : t("emptyFiltered", { kind: t(`filter.${filter}`) })}
          </div>
        ) : (
          <ActivityFeed events={filtered} />
        )}
      </Panel>
    </div>
  );
}

function Stat({
  label,
  value,
  tone = "fg",
}: {
  label: string;
  value: string;
  tone?: "bull" | "fox" | "gold" | "fg" | "muted";
}) {
  const cls =
    tone === "bull"
      ? "text-bull"
      : tone === "fox"
        ? "text-fox-red"
        : tone === "gold"
          ? "text-gold"
          : tone === "muted"
            ? "text-fg-muted"
            : "text-fg";
  return (
    <div className="rounded-xl border border-border-subtle bg-bg-elev/30 px-4 py-3 backdrop-blur-sm">
      <div className="font-mono text-[10px] uppercase tracking-[0.16em] text-fg-muted">
        {label}
      </div>
      <div className={cn("mt-1.5 font-mono text-xl leading-none capitalize", cls)}>
        {value}
      </div>
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
        "rounded-md border px-2 py-0.5 font-mono text-[10px] uppercase tracking-wider transition-colors",
        active
          ? "border-cyan/40 bg-cyan/10 text-cyan"
          : "border-border-subtle text-fg-muted hover:text-fg",
      )}
    >
      {label}
    </button>
  );
}
