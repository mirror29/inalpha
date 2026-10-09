"use client";

import { useMemo, useState } from "react";
import { useLocale, useTranslations } from "next-intl";

import { Link } from "@/i18n/navigation";
import type { EvolutionRunSummary } from "@/lib/types";
import { cn } from "@/lib/cn";
import { evolutionTone, isEvolutionActive } from "@/lib/evolution";
import { fmtRelative } from "@/lib/format";
import { Panel } from "@/components/ui/Panel";
import { StatusBadge } from "@/components/ui/StatusBadge";
import { Td, TableEmpty, TableHeadRow, Th } from "@/components/ui/Table";

type StatusFilter = "all" | "terminal" | "active";
const FILTERS: StatusFilter[] = ["all", "terminal", "active"];

/** 演化运行筛选、表格和 keyset 加载入口。 */
export function EvolutionRunTable({
  runs,
  hasMore,
  isLoadingMore,
  onLoadMore,
}: {
  runs: EvolutionRunSummary[];
  hasMore: boolean;
  isLoadingMore: boolean;
  onLoadMore: () => void;
}) {
  const t = useTranslations("evolution");
  const locale = useLocale();
  const [filter, setFilter] = useState<StatusFilter>("all");
  const rows = useMemo(
    () =>
      filter === "all"
        ? runs
        : runs.filter((run) =>
            filter === "active"
              ? isEvolutionActive(run.status)
              : !isEvolutionActive(run.status),
          ),
    [filter, runs],
  );
  return (
    <Panel
      title={t("evolutionRuns")}
      aside={
        <div className="flex flex-wrap gap-1">
          {FILTERS.map((value) => (
            <FilterChip
              key={value}
              label={t(`filter.${value}`)}
              active={filter === value}
              onClick={() => setFilter(value)}
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
              {rows.map((run) => (
                <RunRow key={run.run_id} run={run} locale={locale} />
              ))}
            </tbody>
          </table>
        </div>
      )}
      {hasMore && (
        <div className="border-t border-border-subtle p-3 text-center">
          <button
            type="button"
            disabled={isLoadingMore}
            onClick={onLoadMore}
            className="rounded-md border border-border-subtle px-3 py-1.5 font-mono text-xs text-fg-muted hover:text-cyan disabled:opacity-50"
          >
            {isLoadingMore ? t("loadingMore") : t("loadMore")}
          </button>
        </div>
      )}
    </Panel>
  );
}

function RunRow({ run, locale }: { run: EvolutionRunSummary; locale: string }) {
  const t = useTranslations("evolution.workflow");
  const c = useTranslations("evolution.costs");
  const ago = fmtRelative(
    run.finished_at ?? run.started_at ?? run.queued_at,
    Date.now(),
    locale,
  );
  return (
    <tr className="border-t border-border-subtle/60 hover:bg-bg-elev/30">
      <Td mono muted>
        <Link href={`/evolution/${run.run_id}`} className="hover:text-cyan">
          {t("baselineEvidence")}
        </Link>
      </Td>
      <Td>
        <StatusBadge
          label={t(`states.${run.status}`)}
          tone={evolutionTone(run.status)}
          dot
          pulse={isEvolutionActive(run.status)}
        />
      </Td>
      <Td>
        <span className="text-fg">
          {typeof run.config.symbol === "string"
            ? run.config.symbol
            : t("marketUnavailable")}
        </span>
      </Td>
      <Td right mono muted>
        {run.budget}
      </Td>
      <Td right mono>
        <span className="text-bull">{run.succeeded}</span>
      </Td>
      <Td right mono muted>
        {run.rejected}
      </Td>
      <Td right mono muted>
        <span title={c("recorded")}>${run.llm_cost_usd.toFixed(4)}</span>
        {(run.unknown_usage_count === undefined || run.unknown_usage_count > 0) && (
          <span className="mt-1 block text-[10px] text-gold">{c("unknownState")}</span>
        )}
      </Td>
      <Td>
        <span className="font-mono text-[11px] text-fg-muted/70">{ago}</span>
      </Td>
    </tr>
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
        "rounded-md border px-2 py-0.5 font-mono text-[10px] uppercase tracking-wider",
        active
          ? "border-cyan/40 bg-cyan/10 text-cyan"
          : "border-border-subtle text-fg-muted hover:text-fg",
      )}
    >
      {label}
    </button>
  );
}
