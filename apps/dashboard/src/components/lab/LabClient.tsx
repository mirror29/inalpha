"use client";

import { useMemo, useState } from "react";
import { useLocale, useTranslations } from "next-intl";
import useSWR from "swr";

import type { LabPayload, StrategyCandidateSummary } from "@/lib/types";
import { Link } from "@/i18n/navigation";
import { cn } from "@/lib/cn";
import { fmtNum } from "@/lib/format";
import { jsonFetcher } from "@/lib/fetcher";
import { ErrorState, SkeletonBlock } from "@/components/ui/Feedback";
import { LiveStrip, Meta } from "@/components/ui/LiveStrip";
import { PageHeader } from "@/components/ui/PageHeader";
import { Panel } from "@/components/ui/Panel";
import { TruncationNote } from "@/components/ui/TruncationNote";
import { CandidateStatusBadge } from "@/components/ui/StatusBadge";
import { Td, TableEmpty, TableHeadRow, Th } from "@/components/ui/Table";

/** 候选/回测慢变,30s 一档(见设计文档轮询节奏)。 */
const REFRESH_MS = 30_000;

type StatusFilter = "all" | "promoted" | "candidate" | "rejected";
const STATUSES: StatusFilter[] = ["all", "promoted", "candidate", "rejected"];

function metric(c: StrategyCandidateSummary, key: string): number | null {
  const v = c.metrics?.[key];
  return typeof v === "number" ? v : null;
}

export function LabClient() {
  const t = useTranslations("lab");
  const tc = useTranslations("common");
  const locale = useLocale();
  const [filter, setFilter] = useState<StatusFilter>("all");

  const { data, error, isValidating, isLoading, mutate } = useSWR<LabPayload>(
    "/api/lab",
    jsonFetcher,
    { refreshInterval: REFRESH_MS, keepPreviousData: true },
  );

  const rows = useMemo(
    () =>
      data
        ? filter === "all"
          ? data.candidates
          : data.candidates.filter((c) => c.status === filter)
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
              label={t("promoted")}
              value={`${data.counts.promoted}/${data.counts.all}`}
              tone={data.counts.promoted > 0 ? "bull" : "muted"}
            />
          </LiveStrip>
        }
      />

      <Panel
        title={t("candidates")}
        aside={
          <div className="flex flex-wrap gap-1">
            {STATUSES.map((s) => (
              <FilterChip
                key={s}
                label={`${t(`filter.${s}`)} ${s === "all" ? data.counts.all : data.counts[s]}`}
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
                  <Th>{t("col.strategy")}</Th>
                  <Th>{t("col.status")}</Th>
                  <Th right>{t("col.fitness")}</Th>
                  <Th right>{t("col.return")}</Th>
                  <Th right>{t("col.sharpe")}</Th>
                  <Th right>{t("col.maxdd")}</Th>
                  <Th right>{t("col.trades")}</Th>
                </TableHeadRow>
              </thead>
              <tbody>
                {rows.map((c) => (
                  <Row key={c.id} c={c} locale={locale} statusLabel={t(`status.${c.status}`)} />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>

      {data.truncated && (
        <TruncationNote text={tc("truncated", { n: data.candidates.length })} />
      )}
    </div>
  );
}

function Row({
  c,
  locale,
  statusLabel,
}: {
  c: StrategyCandidateSummary;
  locale: string;
  statusLabel: string;
}) {
  const ret = metric(c, "total_return_pct");
  const sharpe = metric(c, "sharpe");
  const maxdd = metric(c, "max_drawdown_pct");
  const trades = metric(c, "num_trades");

  return (
    <tr className="border-t border-border-subtle/60 hover:bg-bg-elev/30">
      <Td>
        <Link
          href={`/lab/${c.id}`}
          className="text-fg transition-colors hover:text-cyan"
        >
          {c.description?.trim() || c.code_hash}
        </Link>
        <div className="font-mono text-[10px] text-fg-muted/60">{c.code_hash}</div>
      </Td>
      <Td>
        <CandidateStatusBadge status={c.status} label={statusLabel} />
      </Td>
      <Td right mono>
        <span className={signCls(c.fitness)}>
          {c.fitness === null ? "—" : fmtNum(c.fitness, locale, 3)}
        </span>
      </Td>
      <Td right mono>
        {ret === null ? (
          <Dash />
        ) : (
          <span className={signCls(ret)}>
            {ret > 0 ? "+" : ret < 0 ? "−" : ""}
            {fmtNum(Math.abs(ret), locale, 2)}%
          </span>
        )}
      </Td>
      <Td right mono muted>
        {sharpe === null ? <Dash /> : fmtNum(sharpe, locale, 2)}
      </Td>
      <Td right mono muted>
        {maxdd === null ? <Dash /> : `${fmtNum(maxdd, locale, 2)}%`}
      </Td>
      <Td right mono muted>
        {trades === null ? <Dash /> : fmtNum(trades, locale, 0)}
      </Td>
    </tr>
  );
}

function signCls(v: number | null): string {
  if (v === null) return "text-fg-muted/50";
  if (v > 0) return "text-bull";
  if (v < 0) return "text-fox-red";
  return "text-fg";
}

function Dash() {
  return <span className="text-fg-muted/40">—</span>;
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
