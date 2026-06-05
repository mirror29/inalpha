"use client";

import { useLocale, useNow, useTranslations } from "next-intl";
import { Lock, ShieldCheck, ShieldX } from "lucide-react";
import useSWR from "swr";

import type { RiskLock, RiskPayload } from "@/lib/types";
import { fmtDateTime, fmtRelative } from "@/lib/format";
import { jsonFetcher } from "@/lib/fetcher";
import { ErrorState, SkeletonBlock } from "@/components/ui/Feedback";
import { LiveStrip, Meta } from "@/components/ui/LiveStrip";
import { PageHeader } from "@/components/ui/PageHeader";
import { Panel } from "@/components/ui/Panel";
import { Td, TableEmpty, TableHeadRow, Th } from "@/components/ui/Table";

/** 风控锁实时性较高,8s 一档。 */
const REFRESH_MS = 8000;

export function RiskClient() {
  const t = useTranslations("risk");

  const { data, error, isValidating, isLoading, mutate } = useSWR<RiskPayload>(
    "/api/risk",
    jsonFetcher,
    { refreshInterval: REFRESH_MS, keepPreviousData: true },
  );

  if (isLoading && !data) {
    return (
      <div className="flex flex-col gap-6">
        <SkeletonBlock className="h-16 w-72 border-0 bg-bg-elev/40" />
        <SkeletonBlock className="h-48" />
        <SkeletonBlock className="h-48" />
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
              label={t("engine")}
              value={data.enabled ? t("on") : t("off")}
              tone={data.enabled ? "bull" : "fox"}
            />
            <Meta
              label={t("activeLocks")}
              value={String(data.locks.length)}
              tone={data.locks.length > 0 ? "fox" : "muted"}
            />
          </LiveStrip>
        }
      />

      {/* 活跃锁 —— 放最上(最需要关注)*/}
      <LocksPanel locks={data.locks} title={t("locks")} t={t} />

      {/* 规则配置 */}
      <RulesPanel
        rules={data.rules}
        enabled={data.enabled}
        title={t("rules")}
        emptyLabel={data.enabled ? t("rulesEmpty") : t("disabled")}
      />
    </div>
  );
}

function LocksPanel({
  locks,
  title,
  t,
}: {
  locks: RiskLock[];
  title: string;
  t: ReturnType<typeof useTranslations>;
}) {
  const locale = useLocale();
  const now = useNow({ updateInterval: 10_000 });

  return (
    <Panel
      index="5.1"
      title={title}
      aside={
        <span className="tnum font-mono text-xs text-fg-muted">{locks.length}</span>
      }
    >
      {locks.length === 0 ? (
        <div className="flex flex-col items-center gap-2 px-4 py-10 text-center">
          <ShieldCheck className="size-6 text-bull/70" strokeWidth={1.5} />
          <p className="text-sm text-fg-muted">{t("locksEmpty")}</p>
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-sm">
            <thead>
              <TableHeadRow>
                <Th>{t("col.rule")}</Th>
                <Th>{t("col.scope")}</Th>
                <Th>{t("col.side")}</Th>
                <Th>{t("col.reason")}</Th>
                <Th right>{t("col.until")}</Th>
              </TableHeadRow>
            </thead>
            <tbody>
              {locks.map((l) => (
                <tr
                  key={l.id}
                  className="border-l-2 border-fox-red/60 border-t border-border-subtle/60 bg-fox-red/[0.04]"
                >
                  <Td>
                    <span className="inline-flex items-center gap-1.5 font-medium text-fox-red">
                      <Lock className="size-3" strokeWidth={2} />
                      {l.rule_name}
                    </span>
                  </Td>
                  <Td mono muted>
                    {[l.market, l.symbol].filter(Boolean).join(" ") || l.scope}
                  </Td>
                  <Td mono muted>
                    {l.side}
                  </Td>
                  <Td>
                    <span className="font-mono text-[11px] text-fg-muted">
                      {l.reason}
                    </span>
                  </Td>
                  <Td right mono muted>
                    <span title={fmtDateTime(l.locked_until, locale)}>
                      {fmtRelative(l.locked_until, now.getTime(), locale)}
                    </span>
                  </Td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}

function RulesPanel({
  rules,
  enabled,
  title,
  emptyLabel,
}: {
  rules: { name: string; short_desc: string }[];
  enabled: boolean;
  title: string;
  emptyLabel: string;
}) {
  return (
    <Panel
      index="5.2"
      title={title}
      aside={
        enabled ? (
          <span className="inline-flex items-center gap-1 font-mono text-[10px] uppercase tracking-wider text-bull">
            <ShieldCheck className="size-3" /> on
          </span>
        ) : (
          <span className="inline-flex items-center gap-1 font-mono text-[10px] uppercase tracking-wider text-fox-red">
            <ShieldX className="size-3" /> off
          </span>
        )
      }
    >
      {rules.length === 0 ? (
        <TableEmpty>{emptyLabel}</TableEmpty>
      ) : (
        <ul className="grid grid-cols-1 gap-px bg-border-subtle/40 sm:grid-cols-2">
          {rules.map((r) => (
            <li key={r.name} className="bg-bg-elev/20 px-4 py-3">
              <div className="font-mono text-xs font-medium text-cyan">
                {r.name}
              </div>
              <p className="mt-1 text-sm text-fg-muted">{r.short_desc}</p>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}
