"use client";

import { useMemo } from "react";
import { useTranslations } from "next-intl";
import useSWR from "swr";

import type {
  BacktestRunSummary,
  BacktestTradeRecord,
  BarsPayload,
  StrategyRunDecisionRecord,
} from "@/lib/types";
import { cn } from "@/lib/cn";
import { fmtDateTime, fmtNum, fmtQty, fmtSigned, pnlColor } from "@/lib/format";
import { jsonFetcher } from "@/lib/fetcher";
import { Pager, usePager } from "@/components/ui/Pager";
import { Panel } from "@/components/ui/Panel";
import { Td, TableEmpty, TableHeadRow, Th } from "@/components/ui/Table";
import { CandlestickChart } from "@/components/runners/CandlestickChart";

/**
 * 回测展示三件套(meta 条 / 区间 K 线 / 逐笔成交表)——
 * 候选详情页(/lab/[id])与回测 run 详情页(/backtests/[runId])共用。
 * i18n 统一吃 lab.detail 命名空间(两页文案一致,无需双份 key)。
 */

/** 回测时间 / 区间小条 —— 置于 metrics 面板顶部,标明这组指标的来源回测。 */
export function BacktestMeta({
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
export function BacktestChart({
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
        closed_profit_abs: tr.realized_pnl ?? null,
        closed_profit_pct: null,
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
export function BacktestTradesPanel({
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
