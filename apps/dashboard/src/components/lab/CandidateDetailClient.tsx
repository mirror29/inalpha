"use client";

import { useLocale, useTranslations } from "next-intl";
import { ArrowLeft, CheckCircle2, XCircle } from "lucide-react";
import { useMemo } from "react";
import useSWR from "swr";

import type {
  BacktestRunSummary,
  BacktestTradeRecord,
  BarsPayload,
  CandidateDetailPayload,
  StrategyCandidateRecord,
  StrategyRunDecisionRecord,
  StrategyRunRecord,
} from "@/lib/types";
import { Link } from "@/i18n/navigation";
import { cn } from "@/lib/cn";
import { fmtDateTime, fmtNum, fmtQty, fmtSigned, pnlColor } from "@/lib/format";
import { jsonFetcher } from "@/lib/fetcher";
import { ErrorState, SkeletonBlock } from "@/components/ui/Feedback";
import { LiveStrip } from "@/components/ui/LiveStrip";
import { Pager, usePager } from "@/components/ui/Pager";
import { Panel } from "@/components/ui/Panel";
import { CandidateStatusBadge, RunStatusBadge } from "@/components/ui/StatusBadge";
import { CodeViewer } from "@/components/ui/CodeViewer";
import { Td, TableEmpty, TableHeadRow, Th } from "@/components/ui/Table";
import { CandlestickChart } from "@/components/runners/CandlestickChart";
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
            {/* 回测时间 / 区间 —— 让用户知道这组指标是哪天、跑哪段行情得出的。 */}
            {data.backtestRun && (
              <BacktestMeta run={data.backtestRun} locale={locale} />
            )}
            <div className="p-4">
              {/* 候选自带 metrics(进化期)与最近一次回测 metrics 合并展示;
                  同 key 以回测为准(更新、含专业级扩展指标)。 */}
              <MetricsGrid
                metrics={{ ...c.metrics, ...(data.backtestRun?.metrics ?? {}) }}
                fitness={c.fitness}
              />
            </div>
          </Panel>

          {/* 回测区间 K 线 —— 逐笔成交叠标记;无回测记录时不渲染。 */}
          {data.backtestRun && (
            <BacktestChart run={data.backtestRun} trades={data.backtestTrades} />
          )}

          {/* 回测逐笔成交(含每笔实现盈亏)—— 该候选最近一次回测的买卖复盘。 */}
          <BacktestTradesPanel trades={data.backtestTrades} locale={locale} />

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
            <div className="p-3">
              <CodeViewer
                code={c.code}
                lang="python"
                copyLabel={t("copy")}
                copiedLabel={t("copied")}
              />
            </div>
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

/** 回测时间 / 区间小条 —— 置于 metrics 面板顶部,标明这组指标的来源回测。 */
function BacktestMeta({
  run,
  locale,
}: {
  run: BacktestRunSummary;
  locale: string;
}) {
  const t = useTranslations("lab.detail");
  const period =
    run.periodStart && run.periodEnd
      ? `${fmtDateTime(run.periodStart, locale)} → ${fmtDateTime(run.periodEnd, locale)}`
      : null;
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1 border-b border-border-subtle/60 px-4 py-2 font-mono text-[11px] text-fg-muted">
      <span>
        <span className="text-fg-muted/60">{t("backtestTime")}</span>{" "}
        <span className="text-fg-muted">{fmtDateTime(run.createdAt, locale)}</span>
      </span>
      {period && (
        <span>
          <span className="text-fg-muted/60">{t("backtestPeriod")}</span>{" "}
          <span className="text-fg-muted">{period}</span>
        </span>
      )}
      {run.symbol && (
        <span className="text-fg-muted/70">
          {run.symbol}
          {run.timeframe ? ` · ${run.timeframe}` : ""}
        </span>
      )}
    </div>
  );
}

/**
 * 回测区间 K 线 —— 拉该回测 from→to 的历史 bar(/api/bars 历史区间模式),把逐笔
 * 成交映射成 decision 形状叠在蜡烛上(复用 CandlestickChart 的 buy/sell 标记)。
 * 历史区间固定,不轮询;缺 venue/symbol/区间(老数据)时整块不渲染。
 */
function BacktestChart({
  run,
  trades,
}: {
  run: BacktestRunSummary;
  trades: BacktestTradeRecord[];
}) {
  const t = useTranslations("lab.detail");
  const ready = !!(
    run.venue &&
    run.symbol &&
    run.timeframe &&
    run.periodStart &&
    run.periodEnd
  );
  const key = ready
    ? `/api/bars?venue=${encodeURIComponent(run.venue!)}&symbol=${encodeURIComponent(
        run.symbol!,
      )}&timeframe=${encodeURIComponent(run.timeframe!)}&from=${encodeURIComponent(
        run.periodStart!,
      )}&to=${encodeURIComponent(run.periodEnd!)}&limit=1000`
    : null;
  const { data, error, isLoading } = useSWR<BarsPayload>(key, jsonFetcher, {
    revalidateOnFocus: false,
  });
  // 回测成交 → decision 形状:回测撮合无拒单,outcome 恒 filled。
  const decisions = useMemo<StrategyRunDecisionRecord[]>(
    () =>
      trades.map((tr) => ({
        id: `bt-${tr.seq}`,
        run_id: run.runId,
        bar_ts: tr.bar_ts,
        bar_close: tr.bar_close,
        side: tr.side,
        quantity: tr.quantity,
        order_type: tr.order_type,
        limit_price: null,
        tag: tr.tag,
        intent: tr.intent,
        outcome: "filled" as const,
        fill_price: tr.fill_price,
        fee: tr.fee,
        plan_id: null,
        order_id: null,
        reason: null,
        created_at: tr.bar_ts,
      })),
    [trades, run.runId],
  );

  if (!ready) return null;
  const bars = data?.bars ?? [];
  return (
    <Panel
      title={t("backtestChart")}
      aside={
        <span className="font-mono text-[10px] uppercase tracking-wider text-fg-muted/70">
          {run.symbol} · {run.timeframe}
        </span>
      }
    >
      {bars.length === 0 ? (
        <div className="px-4 py-12 text-center text-sm text-fg-muted/70">
          {isLoading
            ? t("backtestChartLoading")
            : error
              ? t("backtestChartError")
              : t("backtestChartEmpty")}
        </div>
      ) : (
        <div className="px-2 py-2">
          <CandlestickChart bars={bars} decisions={decisions} />
        </div>
      )}
    </Panel>
  );
}

/**
 * 回测逐笔成交表(含每笔实现盈亏)—— 复用 Table 原语,不套 DecisionTimeline
 * (它无 PnL 列且带 live-run 语义)。回测无被拒单,outcome 恒成交,故不设 outcome 列。
 */
function BacktestTradesPanel({
  trades,
  locale,
}: {
  trades: BacktestTradeRecord[];
  locale: string;
}) {
  const t = useTranslations("lab.detail");
  const tIntent = useTranslations("runners.intent");
  // 后端按 seq 升序返回;复盘表按时间倒序展示(最新在上),与 DecisionTimeline 一致。
  const desc = useMemo(
    () => [...trades].sort((a, b) => b.seq - a.seq),
    [trades],
  );
  // 回测成交上限 500 行 —— 分页渲染,一次只挂 25 行 DOM。
  const { page, setPage, pageCount, pageItems } = usePager(desc, 25);

  return (
    <Panel
      title={t("backtestTrades")}
      aside={
        <span className="tnum font-mono text-xs text-fg-muted">
          {trades.length}
        </span>
      }
    >
      {trades.length === 0 ? (
        <TableEmpty>{t("backtestTradesEmpty")}</TableEmpty>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-sm">
            <thead>
              <TableHeadRow>
                <Th>{t("tradeCol.time")}</Th>
                <Th>{t("tradeCol.intent")}</Th>
                <Th>{t("tradeCol.side")}</Th>
                <Th right>{t("tradeCol.qty")}</Th>
                <Th right>{t("tradeCol.fill")}</Th>
                <Th right>{t("tradeCol.fee")}</Th>
                <Th right>{t("tradeCol.pnl")}</Th>
              </TableHeadRow>
            </thead>
            <tbody>
              {pageItems.map((d) => (
                <tr
                  key={d.seq}
                  className="border-t border-border-subtle/60 hover:bg-bg-elev/30"
                >
                  <Td mono muted className="whitespace-nowrap">
                    {fmtBacktestTs(d.bar_ts, locale)}
                  </Td>
                  <Td className="whitespace-nowrap">
                    <span className="font-mono text-[11px] uppercase tracking-wider text-fg">
                      {d.intent ? tIntent(d.intent) : tIntent("unknown")}
                    </span>
                  </Td>
                  <Td>
                    <span
                      className={cn(
                        "font-mono text-xs font-medium uppercase",
                        d.side === "BUY" ? "text-bull" : "text-fox-red",
                      )}
                    >
                      {d.side}
                    </span>
                  </Td>
                  <Td right mono>
                    {fmtQty(d.quantity, locale)}
                  </Td>
                  <Td right mono>
                    {d.fill_price === null ? (
                      <span className="text-fg-muted/50">—</span>
                    ) : (
                      fmtNum(d.fill_price, locale, 4)
                    )}
                  </Td>
                  <Td right mono muted>
                    {d.fee === null || d.fee === 0 ? (
                      <span className="text-fg-muted/50">—</span>
                    ) : (
                      fmtNum(d.fee, locale, 4)
                    )}
                  </Td>
                  <Td right mono>
                    {d.realized_pnl === null || d.realized_pnl === 0 ? (
                      <span className="text-fg-muted/50">—</span>
                    ) : (
                      <span className={pnlColor(d.realized_pnl)}>
                        {fmtSigned(d.realized_pnl, null, locale)}
                      </span>
                    )}
                  </Td>
                </tr>
              ))}
            </tbody>
          </table>
          <Pager page={page} pageCount={pageCount} onChange={setPage} />
        </div>
      )}
    </Panel>
  );
}

/** 回测成交时间到分钟(跨多根 bar,带日期)。 */
function fmtBacktestTs(iso: string, locale: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return new Intl.DateTimeFormat(locale, {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(d);
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
