"use client";

import { useLocale, useTranslations } from "next-intl";
import { ArrowLeft, CheckCircle2, XCircle } from "lucide-react";
import useSWR from "swr";

import type {
  CandidateDetailPayload,
  StrategyCandidateRecord,
  StrategyRunRecord,
} from "@/lib/types";
import { Link } from "@/i18n/navigation";
import { cn } from "@/lib/cn";
import { fmtDateTime, fmtSigned, pnlColor } from "@/lib/format";
import { jsonFetcher } from "@/lib/fetcher";
import { ErrorState, SkeletonBlock } from "@/components/ui/Feedback";
import { LiveStrip } from "@/components/ui/LiveStrip";
import { Panel } from "@/components/ui/Panel";
import { CandidateStatusBadge, RunStatusBadge } from "@/components/ui/StatusBadge";
import { DecisionTimeline } from "@/components/runners/DecisionTimeline";
import { RunnerChart } from "@/components/runners/RunnerChart";
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

          <Panel title={t("metrics")}>
            <div className="p-4">
              <MetricsGrid metrics={c.metrics} fitness={c.fitness} />
            </div>
          </Panel>

          {/* 操作日志:由现有状态(创建/晋级/各 run 启停)派生的统一时间线 —— agent 对这条
              策略做过的事一目了然,便于回溯。 */}
          <OpsLog candidate={c} runs={data.runs} locale={locale} />

          {/* 执行记录:该策略派生的 live runner + 最近一个 run 的 K 线 / 历史交易。 */}
          <RunInstancesPanel runs={data.runs} locale={locale} />
          {data.runs[0] && (
            <>
              <RunnerChart
                venue={data.runs[0].venue}
                symbol={data.runs[0].symbol}
                timeframe={data.runs[0].timeframe}
                decisions={data.latestRunDecisions}
              />
              <DecisionTimeline decisions={data.latestRunDecisions} />
            </>
          )}

          {c.audit && <AuditPanel audit={c.audit} />}

          <Panel title={t("code")}>
            <pre className="max-h-[28rem] overflow-auto px-4 py-3 font-mono text-[12px] leading-relaxed text-fg-muted">
              <code>{c.code}</code>
            </pre>
          </Panel>
        </>
      )}
    </div>
  );
}

/** 操作日志一条(由现有状态派生)。 */
type OpEvent = {
  ts: string;
  type: "created" | "promoted" | "started" | "stopped" | "errored";
  /** 副信息:晋级理由 / run 标的。 */
  detail?: string;
  /** 发起方(author / promoted_by)。 */
  by?: string;
  /** 关联 run —— 有则整条可点进 run 详情。 */
  runId?: string;
};

/** 操作类型 → 状态灯颜色。 */
const OP_DOT: Record<OpEvent["type"], string> = {
  created: "bg-fg-muted/50",
  promoted: "bg-bull",
  started: "bg-cyan",
  stopped: "bg-fg-muted/50",
  errored: "bg-fox-red",
};

/**
 * 策略操作日志 —— 从现有状态派生的统一时间线(不额外请求 / 不改后端):
 * 创建(created_at + author)、晋级(audit.promotion)、各 run 的启动 / 停止 / 出错。
 * 让「agent 对这条策略做过什么」一眼可回溯。
 */
function OpsLog({
  candidate,
  runs,
  locale,
}: {
  candidate: StrategyCandidateRecord;
  runs: StrategyRunRecord[];
  locale: string;
}) {
  const t = useTranslations("lab.detail");

  const events: OpEvent[] = [];
  // 创建
  events.push({
    ts: candidate.created_at,
    type: "created",
    by: candidate.author,
  });
  // 晋级(audit.promotion 里有就加)
  const promotion =
    candidate.audit && typeof candidate.audit["promotion"] === "object"
      ? (candidate.audit["promotion"] as Record<string, unknown>)
      : null;
  if (promotion && typeof promotion["promoted_at"] === "string") {
    events.push({
      ts: promotion["promoted_at"],
      type: "promoted",
      detail:
        typeof promotion["reason"] === "string" ? promotion["reason"] : undefined,
      by:
        typeof promotion["promoted_by"] === "string"
          ? promotion["promoted_by"]
          : undefined,
    });
  }
  // 各 run 的启动 / 停止(停止态才有 stopped_at)
  for (const r of runs) {
    const inst = `${r.symbol} · ${r.venue} · ${r.timeframe}`;
    events.push({ ts: r.started_at, type: "started", detail: inst, runId: r.id });
    if (r.stopped_at) {
      events.push({
        ts: r.stopped_at,
        type: r.status === "errored" ? "errored" : "stopped",
        detail: inst,
        runId: r.id,
      });
    }
  }
  // 最新在上
  events.sort((a, b) => b.ts.localeCompare(a.ts));

  return (
    <Panel
      title={t("opsLog")}
      aside={
        <span className="tnum font-mono text-xs text-fg-muted">
          {events.length}
        </span>
      }
    >
      <p className="border-b border-border-subtle/60 px-4 py-2 text-[11px] text-fg-muted/70">
        {t("opsLogHint")}
      </p>
      <ul className="divide-y divide-border-subtle/60">
        {events.map((e, i) => {
          const row = (
            <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1 px-4 py-2.5">
              <span
                className={cn(
                  "inline-block size-2 shrink-0 rounded-full",
                  OP_DOT[e.type],
                )}
              />
              <span className="text-sm text-fg">{t(`op.${e.type}`)}</span>
              {e.detail && (
                <span className="font-mono text-[11px] text-fg-muted">
                  {e.detail}
                </span>
              )}
              {e.by && (
                <span className="font-mono text-[10px] uppercase tracking-wider text-fg-muted/60">
                  {t("op.by", { who: e.by })}
                </span>
              )}
              <span className="tnum ml-auto font-mono text-[10px] text-fg-muted/60 tabular-nums">
                {fmtDateTime(e.ts, locale)}
              </span>
            </div>
          );
          return (
            <li key={`${e.ts}-${e.type}-${i}`}>
              {e.runId ? (
                <Link
                  href={`/runners/${e.runId}`}
                  className="block transition-colors hover:bg-bg-elev/30"
                >
                  {row}
                </Link>
              ) : (
                row
              )}
            </li>
          );
        })}
      </ul>
    </Panel>
  );
}

/** 该候选派生的 live runner 列表 —— 行可点进 run 详情。 */
function RunInstancesPanel({
  runs,
  locale,
}: {
  runs: StrategyRunRecord[];
  locale: string;
}) {
  const t = useTranslations("lab.detail");
  const tStatus = useTranslations("runners");

  return (
    <Panel
      title={t("instances")}
      aside={
        <span className="tnum font-mono text-xs text-fg-muted">
          {runs.length}
        </span>
      }
    >
      {runs.length === 0 ? (
        <p className="px-4 py-6 text-center text-sm text-fg-muted/70">
          {t("instancesEmpty")}
        </p>
      ) : (
        <>
          <p className="border-b border-border-subtle/60 px-4 py-2 text-[11px] text-fg-muted/70">
            {t("instancesHint")}
          </p>
          <ul className="divide-y divide-border-subtle/60">
            {runs.map((r) => (
              <li key={r.id}>
                <Link
                  href={`/runners/${r.id}`}
                  className="flex flex-wrap items-center gap-x-3 gap-y-1 px-4 py-2.5 transition-colors hover:bg-bg-elev/30"
                >
                  <RunStatusBadge status={r.status} />
                  <span className="font-mono text-xs text-fg">
                    {r.symbol}
                  </span>
                  <span className="font-mono text-[11px] text-fg-muted">
                    {r.venue} · {r.timeframe}
                  </span>
                  <span
                    className={cn(
                      "tnum ml-auto font-mono text-xs",
                      pnlColor(r.cumulative_pnl),
                    )}
                  >
                    {fmtSigned(r.cumulative_pnl, null, locale)}
                  </span>
                  <span className="font-mono text-[10px] text-fg-muted/60 tabular-nums">
                    {fmtDateTime(r.started_at, locale)}
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        </>
      )}
    </Panel>
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
    <Panel title={t("audit")}>
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
