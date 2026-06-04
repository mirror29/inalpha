"use client";

import { useLocale, useTranslations } from "next-intl";
import { ArrowLeft, CheckCircle2, XCircle } from "lucide-react";
import useSWR from "swr";

import type { CandidateDetailPayload } from "@/lib/types";
import { Link } from "@/i18n/navigation";
import { cn } from "@/lib/cn";
import { fmtDateTime } from "@/lib/format";
import { jsonFetcher } from "@/lib/fetcher";
import { ErrorState, SkeletonBlock } from "@/components/ui/Feedback";
import { LiveStrip } from "@/components/ui/LiveStrip";
import { Panel } from "@/components/ui/Panel";
import { CandidateStatusBadge } from "@/components/ui/StatusBadge";
import { MetricsGrid } from "./MetricsGrid";

const REFRESH_MS = 30_000;

export function CandidateDetailClient({ id }: { id: string }) {
  const t = useTranslations("lab.detail");
  const tStatus = useTranslations("lab.status");
  const locale = useLocale();

  const { data, error, isValidating, isLoading, mutate } =
    useSWR<CandidateDetailPayload>(`/api/lab/${id}`, jsonFetcher, {
      refreshInterval: REFRESH_MS,
      keepPreviousData: true,
    });

  if (isLoading && !data) {
    return (
      <div className="flex flex-col gap-6">
        <SkeletonBlock className="h-16 border-0 bg-bg-elev/40" />
        <SkeletonBlock className="h-80" />
      </div>
    );
  }
  // 404 → fetcher 抛错;无任何帧时给错误态(含"未找到")。
  if (error && !data) {
    return (
      <ErrorState
        message={error instanceof Error ? error.message : String(error)}
        onRetry={() => mutate()}
      />
    );
  }
  if (!data) return null;
  const c = data.candidate;

  return (
    <div className="flex flex-col gap-6">
      <Link
        href="/lab"
        className="inline-flex w-fit items-center gap-1.5 font-mono text-xs text-fg-muted transition-colors hover:text-cyan"
      >
        <ArrowLeft className="size-3.5" />
        {t("back")}
      </Link>

      {c === null ? (
        <div className="rounded-xl border border-dashed border-border-subtle py-20 text-center text-sm text-fg-muted">
          {t("notFound")}
        </div>
      ) : (
        <>
          <header className="flex flex-col gap-3 border-b border-border-subtle pb-5">
            <div className="flex flex-wrap items-center gap-3">
              <h1 className="font-display text-2xl text-fg lg:text-3xl">
                {c.description?.trim() || c.code_hash}
              </h1>
              <CandidateStatusBadge status={c.status} label={tStatus(c.status)} />
            </div>
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div className="font-mono text-xs text-fg-muted">
                {c.author} · {c.code_hash} · {fmtDateTime(c.created_at, locale)}
              </div>
              <LiveStrip
                asOf={data.asOf}
                isValidating={isValidating}
                isStaleFrame={Boolean(error)}
              />
            </div>
          </header>

          <Panel index="4.2" title={t("metrics")}>
            <div className="p-4">
              <MetricsGrid metrics={c.metrics} fitness={c.fitness} />
            </div>
          </Panel>

          {c.audit && <AuditPanel audit={c.audit} />}

          <Panel index="4.4" title={t("code")}>
            <pre className="max-h-[28rem] overflow-auto px-4 py-3 font-mono text-[12px] leading-relaxed text-fg-muted">
              <code>{c.code}</code>
            </pre>
          </Panel>
        </>
      )}
    </div>
  );
}

function AuditPanel({ audit }: { audit: Record<string, unknown> }) {
  const t = useTranslations("lab.detail");
  const ok = audit["ok"] === true;
  const className = typeof audit["class_name"] === "string" ? audit["class_name"] : null;
  const findings = Array.isArray(audit["findings"]) ? (audit["findings"] as unknown[]) : [];
  const promotion =
    audit["promotion"] && typeof audit["promotion"] === "object"
      ? (audit["promotion"] as Record<string, unknown>)
      : null;

  return (
    <Panel index="4.3" title={t("audit")}>
      <div className="flex flex-col gap-3 p-4">
        <div className="flex flex-wrap items-center gap-4 text-sm">
          <span
            className={cn(
              "inline-flex items-center gap-1.5 font-mono text-xs",
              ok ? "text-bull" : "text-fox-red",
            )}
          >
            {ok ? (
              <CheckCircle2 className="size-4" strokeWidth={2} />
            ) : (
              <XCircle className="size-4" strokeWidth={2} />
            )}
            {ok ? "AUDIT OK" : "AUDIT FAILED"}
          </span>
          {className && (
            <span className="font-mono text-xs text-fg-muted">
              class <span className="text-fg">{className}</span>
            </span>
          )}
          {findings.length > 0 && (
            <span className="font-mono text-xs text-gold">
              {findings.length} finding(s)
            </span>
          )}
        </div>

        {promotion && (
          <div className="rounded-lg border border-bull/25 bg-bull/[0.06] px-3 py-2 text-sm">
            <div className="font-mono text-[10px] uppercase tracking-wider text-bull/80">
              {t("promotion")}
            </div>
            {typeof promotion["reason"] === "string" && (
              <p className="mt-1 text-fg-muted">{promotion["reason"] as string}</p>
            )}
            <div className="mt-1 font-mono text-[11px] text-fg-muted/70">
              {[promotion["promoted_by"], promotion["promoted_at"]]
                .filter((x) => typeof x === "string")
                .join(" · ")}
            </div>
          </div>
        )}

        {findings.length > 0 && (
          <ul className="flex flex-col gap-1 font-mono text-[11px] text-fox-red">
            {findings.map((f, i) => (
              <li key={i} className="break-all">
                {typeof f === "string" ? f : JSON.stringify(f)}
              </li>
            ))}
          </ul>
        )}
      </div>
    </Panel>
  );
}
