"use client";

import { useLocale, useNow, useTranslations } from "next-intl";
import { ArrowLeft, CircleAlert, Info, TriangleAlert } from "lucide-react";
import useSWR from "swr";

import type {
  RunDetailPayload,
  RunLogEntry,
  RunLogLevel,
  StrategyRunDecisionRecord,
  StrategyRunRecord,
} from "@/lib/types";
import { Link } from "@/i18n/navigation";
import { cn } from "@/lib/cn";
import { fmtRelative, fmtSigned, pnlColor } from "@/lib/format";
import { jsonFetcher } from "@/lib/fetcher";
import { ErrorState, SkeletonBlock } from "@/components/ui/Feedback";
import { LiveStrip } from "@/components/ui/LiveStrip";
import { Panel } from "@/components/ui/Panel";
import { RunStatusBadge } from "@/components/ui/StatusBadge";
import { DecisionTimeline } from "./DecisionTimeline";
import { RunnerChart } from "./RunnerChart";

const REFRESH_MS = 6000;

export function RunnerDetailClient({ runId }: { runId: string }) {
  const t = useTranslations("runners.detail");
  const locale = useLocale();

  const { data, error, isValidating, isLoading, mutate } =
    useSWR<RunDetailPayload>(`/api/runners/${runId}`, jsonFetcher, {
      refreshInterval: REFRESH_MS,
      keepPreviousData: true,
    });

  if (isLoading && !data) {
    return (
      <div className="flex flex-col gap-6">
        <SkeletonBlock className="h-16 border-0 bg-bg-elev/40" />
        <SkeletonBlock className="h-72" />
      </div>
    );
  }

  // 404(run 不存在)→ fetcher 抛 FetchError;无任何帧时给错误态。
  if (error && !data) {
    return (
      <ErrorState
        message={error instanceof Error ? error.message : String(error)}
        onRetry={() => mutate()}
      />
    );
  }

  if (!data) return null;
  const run = data.run;

  return (
    <div className="flex flex-col gap-6">
      <BackLink label={t("back")} />

      {run === null ? (
        <div className="rounded-xl border border-dashed border-border-subtle py-20 text-center text-sm text-fg-muted">
          {t("notFound")}
        </div>
      ) : (
        <>
          {/* 头部:标的 + 状态 + 累计盈亏 + LIVE */}
          <header className="flex flex-col gap-4 border-b border-border-subtle pb-5 lg:flex-row lg:items-end lg:justify-between">
            <div>
              <div className="flex items-center gap-3">
                <h1 className="font-display text-3xl text-fg lg:text-4xl">
                  {run.symbol}
                </h1>
                <RunStatusBadge status={run.status} />
              </div>
              <div className="mt-1.5 font-mono text-xs text-fg-muted">
                {run.venue} · {run.timeframe}
              </div>
              {/* 当前所跑策略 —— 可点进策略详情(模拟盘 → 策略可追溯)。 */}
              <div className="mt-1 flex items-center gap-1.5 text-xs">
                <span className="shrink-0 whitespace-nowrap font-mono uppercase tracking-[0.14em] text-fg-muted/70">
                  {t("strategy")}
                </span>
                <Link
                  href={`/lab/${run.candidate_id}`}
                  title={t("viewStrategy")}
                  className="truncate text-cyan transition-colors hover:text-cyan/80 hover:underline"
                >
                  {data.candidate?.description?.trim() ||
                    `cand ${run.candidate_id.slice(0, 8)}`}
                </Link>
              </div>
            </div>
            <LiveStrip
              asOf={data.asOf}
              isValidating={isValidating}
              isStaleFrame={Boolean(error)}
            />
          </header>

          {/* 当前模拟盘指标条(置于 K 线上方)。 */}
          <RunnerStats run={run} decisions={data.decisions} />

          {/* K 线置顶 —— 决策点叠在蜡烛上,先看价格走势。 */}
          <RunnerChart
            venue={run.venue}
            symbol={run.symbol}
            timeframe={run.timeframe}
            decisions={data.decisions}
          />

          <DecisionTimeline decisions={data.decisions} />

          {/* 运行日志置底 —— agent 全量活动(起跑 / 出单 / 停止 / 退避 / 错误),按级别着色。 */}
          <RunLog entries={run.run_log} />
        </>
      )}
    </div>
  );
}

/** 当前模拟盘指标条:累计盈亏 / 决策数 / 风控拦截 / 最后 bar。 */
function RunnerStats({
  run,
  decisions,
}: {
  run: StrategyRunRecord;
  decisions: StrategyRunDecisionRecord[];
}) {
  const t = useTranslations("runners.stats");
  const locale = useLocale();
  const now = useNow({ updateInterval: 10_000 });
  const filled = decisions.filter((d) => d.outcome === "filled").length;
  const blocked = decisions.filter((d) => d.outcome === "risk_rejected").length;

  return (
    <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
      <StatCard label={t("cumulativePnl")} accent>
        <Figure className={pnlColor(run.cumulative_pnl)}>
          {fmtSigned(run.cumulative_pnl, null, locale)}
        </Figure>
      </StatCard>
      <StatCard label={t("decisions")}>
        <Figure>{decisions.length}</Figure>
        <Sub>{t("filledOf", { filled })}</Sub>
      </StatCard>
      <StatCard label={t("blocked")}>
        <Figure className={blocked > 0 ? "text-fox-red" : undefined}>
          {blocked}
        </Figure>
      </StatCard>
      <StatCard label={t("lastBar")}>
        <Figure className="text-lg">
          {run.last_bar_ts
            ? fmtRelative(run.last_bar_ts, now.getTime(), locale)
            : t("neverRan")}
        </Figure>
      </StatCard>
    </div>
  );
}

function StatCard({
  label,
  accent,
  children,
}: {
  label: string;
  accent?: boolean;
  children: React.ReactNode;
}) {
  return (
    <div className="relative overflow-hidden rounded-xl border border-border-subtle bg-bg-elev/30 px-4 py-3 backdrop-blur-sm">
      {accent && <span className="absolute inset-x-0 top-0 h-px bg-cyan/50" />}
      <div className="font-mono text-[10px] uppercase tracking-[0.16em] text-fg-muted">
        {label}
      </div>
      <div className="mt-1.5">{children}</div>
    </div>
  );
}

function Figure({
  children,
  className,
}: {
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "tnum font-mono text-2xl leading-none tracking-tight text-fg",
        className,
      )}
    >
      {children}
    </div>
  );
}

function Sub({ children }: { children: React.ReactNode }) {
  return (
    <div className="tnum mt-1 font-mono text-[11px] text-fg-muted/80">
      {children}
    </div>
  );
}

function BackLink({ label }: { label: string }) {
  return (
    <Link
      href="/runners"
      className="inline-flex w-fit items-center gap-1.5 font-mono text-xs text-fg-muted transition-colors hover:text-cyan"
    >
      <ArrowLeft className="size-3.5" />
      {label}
    </Link>
  );
}

/** 日志级别 → 图标 + 颜色（info=电光青 / warn=金 / error=朱红）。 */
const LEVEL_STYLE: Record<RunLogLevel, { icon: typeof Info; cls: string }> = {
  info: { icon: Info, cls: "text-cyan" },
  warn: { icon: TriangleAlert, cls: "text-gold" },
  error: { icon: CircleAlert, cls: "text-fox-red" },
};

/** 运行日志面板 —— 全量活动按级别着色,最新在上。 */
function RunLog({ entries }: { entries: RunLogEntry[] }) {
  const t = useTranslations("runners.detail");
  // 后端按时间序追加(最新在末尾),这里倒序展示——最近活动一眼可见。
  const rows = [...entries].reverse();

  return (
    <Panel
      title={t("runLog")}
      aside={
        <span className="tnum font-mono text-xs text-fg-muted">{entries.length}</span>
      }
    >
      {rows.length === 0 ? (
        <p className="px-4 py-6 text-center text-sm text-fg-muted/70">
          {t("runLogEmpty")}
        </p>
      ) : (
        <ul className="divide-y divide-border-subtle/60">
          {rows.map((e, i) => {
            const lvl: RunLogLevel =
              e.level === "warn" || e.level === "error" ? e.level : "info";
            const { icon: Icon, cls } = LEVEL_STYLE[lvl];
            return (
              <li key={i} className="flex items-start gap-2.5 px-4 py-2 font-mono text-[11px]">
                <Icon className={cn("mt-0.5 size-3 shrink-0", cls)} strokeWidth={2} />
                <span className="shrink-0 text-fg-muted/60">{fmtLogTs(e.ts)}</span>
                <span className="break-all text-fg-muted">
                  {e.msg}
                  {e.code ? <span className="ml-1.5 text-fg-muted/50">[{e.code}]</span> : null}
                </span>
              </li>
            );
          })}
        </ul>
      )}
    </Panel>
  );
}

/**
 * 日志时间戳 —— 后端写 `NOW()::text`(形如 "2026-06-08 11:35:00.12+00"，带时区偏移)。
 * 按**用户本地时区**显示月日时分秒：否则 UTC+8 用户会把 UTC 的 "11:35" 误读成本地 11:35
 * (实为本地 19:35)，排障对不上运行记录(§3 面向全球用户)。解析失败回退原串截取。
 */
function fmtLogTs(ts: string): string {
  // 空格→T、裸偏移 "+00"→"+00:00" 以满足跨浏览器 ISO 解析；偏移由串自带,不硬编码 UTC。
  const iso = ts.replace(" ", "T").replace(/([+-]\d{2})$/, "$1:00");
  const d = new Date(iso);
  if (!Number.isNaN(d.getTime())) {
    return new Intl.DateTimeFormat(undefined, {
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false,
    }).format(d);
  }
  const m = ts.match(/^\d{4}-(\d{2}-\d{2})[ T](\d{2}:\d{2}:\d{2})/);
  return m ? `${m[1]} ${m[2]}` : ts;
}
