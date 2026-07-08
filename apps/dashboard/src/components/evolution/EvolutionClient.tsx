"use client";

import { useMemo, useState } from "react";
import { useLocale, useTranslations } from "next-intl";
import { Workflow } from "lucide-react";
import useSWR from "swr";

import type { EvolutionPayload } from "@/lib/types";
import { cn } from "@/lib/cn";
import { fmtNum, fmtRelative } from "@/lib/format";
import { jsonFetcher } from "@/lib/fetcher";
import { ErrorState, SkeletonBlock } from "@/components/ui/Feedback";
import { LiveStrip } from "@/components/ui/LiveStrip";
import { PageHeader } from "@/components/ui/PageHeader";
import { Panel } from "@/components/ui/Panel";
import { StatusBadge } from "@/components/ui/StatusBadge";
import { TruncationNote } from "@/components/ui/TruncationNote";
import { Td, TableEmpty, TableHeadRow, Th } from "@/components/ui/Table";

/** 演化运行慢变，30s 一档。 */
const REFRESH_MS = 30_000;

type StatusFilter = "all" | "done" | "running";
const FILTERS: StatusFilter[] = ["all", "done", "running"];

function runTone(status: string) {
  if (status === "completed") return "bull";
  if (status === "running") return "cyan";
  if (status === "failed") return "fox";
  return "muted";
}

function runStatusLabel(status: string) {
  if (status === "completed") return "DONE";
  if (status === "running") return "RUNNING";
  if (status === "failed") return "FAILED";
  return status.toUpperCase();
}

export function EvolutionClient() {
  const t = useTranslations("evolution");
  const locale = useLocale();
  const [filter, setFilter] = useState<StatusFilter>("all");

  const { data, error, isValidating, isLoading, mutate } = useSWR<EvolutionPayload>(
    "/api/evolution",
    jsonFetcher,
    { refreshInterval: REFRESH_MS, keepPreviousData: true },
  );

  const rows = useMemo(
    () =>
      data
        ? filter === "all"
          ? data.runs
          : data.runs.filter(
              (r) => r.status === (filter === "done" ? "completed" : "running"),
            )
        : [],
    [data, filter],
  );

  if (isLoading && !data) {
    return (
      <div className="flex flex-col gap-6">
        <SkeletonBlock className="h-16 w-72 border-0 bg-bg-elev/40" />
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

  const running = data.runs.filter((r) => r.status === "running").length;
  const totalCost = data.runs.reduce((s, r) => s + r.llm_cost_usd, 0);

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
        <Stat label={t("runs")} value={String(data.runs.length)} tone="bull" />
        <Stat
          label={t("running")}
          value={String(running)}
          tone={running > 0 ? "cyan" : "muted"}
        />
        <Stat label={t("llmCost")} value={`$${totalCost.toFixed(4)}`} tone="muted" />
        <Stat
          label={t("rejected")}
          value={String(
            data.runs.reduce((s, r) => s + r.rejected_ast + r.rejected_contract + r.failed_eval, 0),
          )}
          tone="fox"
        />
      </div>

      <Panel
        title={t("evolutionRuns")}
        aside={
          <div className="flex flex-wrap gap-1">
            {FILTERS.map((s) => (
              <FilterChip
                key={s}
                label={t(`filter.${s}`)}
                active={filter === s}
                onClick={() => setFilter(s)}
              />
            ))}
          </div>
        }
      >
        {rows.length === 0 ? (
          <TableEmpty>{t("empty")}</TableEmpty>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full border-collapse text-sm">
              <thead>
                <TableHeadRow>
                  <Th>{t("col.run")}</Th>
                  <Th>{t("col.status")}</Th>
                  <Th>{t("col.seed")}</Th>
                  <Th right>{t("col.budget")}</Th>
                  <Th right>{t("col.candidates")}</Th>
                  <Th right>{t("col.rejected")}</Th>
                  <Th right>{t("col.cost")}</Th>
                  <Th>{t("col.time")}</Th>
                </TableHeadRow>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <Row key={r.run_id} r={r} locale={locale} t={t} />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>

      {data.truncated && (
        <TruncationNote text={t("truncated", { n: data.runs.length })} />
      )}
    </div>
  );
}

function Row({
  r,
  locale,
  t,
}: {
  r: EvolutionPayload["runs"][number];
  locale: string;
  t: ReturnType<typeof useTranslations>;
}) {
  const nowMs = Date.now();
  const ago = fmtRelative(r.finished_at ?? r.started_at, nowMs, locale);
  return (
    <tr className="border-t border-border-subtle/60 hover:bg-bg-elev/30">
      <Td mono muted>
        {r.run_id.slice(0, 8)}
      </Td>
      <Td>
        <StatusBadge label={runStatusLabel(r.status)} tone={runTone(r.status)} dot pulse={r.status === "running"} />
      </Td>
      <Td>
        <span className="text-fg">{r.seed_strategy_id}</span>
      </Td>
      <Td right mono muted>
        {r.budget}
      </Td>
      <Td right mono>
        <span className="text-bull">{r.candidates_count}</span>
      </Td>
      <Td right mono muted>
        {r.rejected_ast + r.rejected_contract + r.failed_eval}
      </Td>
      <Td right mono muted>
        ${r.llm_cost_usd.toFixed(4)}
      </Td>
      <Td>
        <span className="font-mono text-[11px] text-fg-muted/70">{ago}</span>
      </Td>
    </tr>
  );
}

function Stat({
  label,
  value,
  tone = "fg",
}: {
  label: string;
  value: string;
  tone?: string;
}) {
  return (
    <div className="rounded-xl border border-border-subtle bg-bg-elev/30 px-4 py-3 backdrop-blur-sm">
      <div className="font-mono text-[10px] uppercase tracking-[0.16em] text-fg-muted">
        {label}
      </div>
      <div className={`mt-1.5 font-mono text-xl leading-none capitalize text-${tone}`}>
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