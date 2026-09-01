"use client";

import { useLocale, useTranslations } from "next-intl";
import useSWR from "swr";

import { jsonFetcher } from "@/lib/fetcher";
import { fmtRelative } from "@/lib/format";
import type { EventDataCoverage } from "@/lib/types";
import { ErrorState, SkeletonBlock } from "@/components/ui/Feedback";
import { LiveStrip } from "@/components/ui/LiveStrip";
import { PageHeader } from "@/components/ui/PageHeader";
import { Panel } from "@/components/ui/Panel";
import { Td, TableEmpty, TableHeadRow, Th } from "@/components/ui/Table";

/** Operational view for the append-only event ledger and source freshness. */
export function DataHealthClient() {
  const t = useTranslations("dataHealth");
  const locale = useLocale();
  const { data, error, isLoading, isValidating, mutate } = useSWR<EventDataCoverage>(
    "/api/data-health",
    jsonFetcher,
    { refreshInterval: 30_000, keepPreviousData: true },
  );
  if (isLoading && !data) return <SkeletonBlock className="h-96" />;
  if (error && !data) return <ErrorState message={error instanceof Error ? error.message : String(error)} onRetry={() => mutate()} />;
  if (!data) return null;
  const cards = [
    [t("rawEvents"), data.raw_event_count],
    [t("facts"), data.fact_count],
    [t("retractions"), data.retraction_count],
    [t("lastAccepted"), data.latest_accepted_at ? fmtRelative(data.latest_accepted_at, Date.now(), locale) : "—"],
  ] as const;
  return <div className="flex flex-col gap-6"><PageHeader title={t("title")} subtitle={t("subtitle")} right={<LiveStrip asOf={data.as_of} isValidating={isValidating} isStaleFrame={Boolean(error)} />} /><div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">{cards.map(([label, value]) => <div key={label} className="rounded-xl border border-border-subtle bg-bg-elev/30 p-4"><div className="font-mono text-[10px] uppercase tracking-wider text-fg-muted">{label}</div><div className="mt-2 font-mono text-2xl text-fg">{value}</div></div>)}</div><Panel title={t("sources")}>{data.sources.length === 0 ? <TableEmpty>{t("empty")}</TableEmpty> : <div className="overflow-x-auto"><table className="w-full"><thead><TableHeadRow><Th>{t("source")}</Th><Th right>{t("count")}</Th><Th right>{t("versions")}</Th><Th>{t("latest")}</Th></TableHeadRow></thead><tbody>{data.sources.map((source) => <tr key={source.source} className="border-t border-border-subtle/60"><Td mono>{source.source}</Td><Td right mono>{source.raw_event_count}</Td><Td right mono muted>{source.max_version}</Td><Td mono muted>{source.latest_accepted_at ? fmtRelative(source.latest_accepted_at, Date.now(), locale) : "—"}</Td></tr>)}</tbody></table></div>}</Panel></div>;
}
