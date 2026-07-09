"use client";

import { useLocale, useTranslations } from "next-intl";

import type { StrategyRunDecisionRecord } from "@/lib/types";
import { cn } from "@/lib/cn";
import { fmtNum, fmtQty, fmtSigned, pnlColor } from "@/lib/format";
import { Pager, usePager } from "@/components/ui/Pager";
import { Panel } from "@/components/ui/Panel";
import { DecisionOutcomeBadge } from "@/components/ui/StatusBadge";
import { Td, TableEmpty, TableHeadRow, Th } from "@/components/ui/Table";

/** 决策一页 25 行 —— 一屏内看全,200 条上限时 8 页。 */
const PAGE_SIZE = 25;

/**
 * 决策复盘时间线 —— 每根产生下单意图的 bar 一行。
 * risk_rejected 行整行染红底,reason 直接展示风控拒单理由(这是用户最常问 agent 的
 * "那一单为什么没成")。
 */
export function DecisionTimeline({
  decisions,
  maxBodyHeight,
}: {
  decisions: StrategyRunDecisionRecord[];
  /** 可选表体最大高度(tailwind max-h-*):并排布局时限高,内部滚动 + 表头吸顶。 */
  maxBodyHeight?: string;
}) {
  const t = useTranslations("runners.detail");
  const tIntent = useTranslations("runners.intent");
  const tOutcome = useTranslations("runners.outcome");
  const locale = useLocale();
  const { page, setPage, pageCount, pageItems } = usePager(decisions, PAGE_SIZE);

  return (
    <Panel
      title={t("decisions")}
      aside={
        <span className="tnum font-mono text-xs text-fg-muted">
          {decisions.length}
        </span>
      }
    >
      {decisions.length === 0 ? (
        <TableEmpty>{t("decisionsEmpty")}</TableEmpty>
      ) : (
        <div
          className={cn(
            "overflow-x-auto",
            maxBodyHeight && cn("overflow-y-auto", maxBodyHeight),
          )}
        >
          <table className="w-full border-collapse text-sm">
            <thead
              className={cn(
                // 限高滚动时表头吸顶(不透明底,行从下面滚过)。
                maxBodyHeight && "sticky top-0 z-10 bg-bg-elev",
              )}
            >
              <TableHeadRow>
                <Th>{t("col.barTs")}</Th>
                <Th right>{t("col.barClose")}</Th>
                <Th>{t("col.intent")}</Th>
                <Th>{t("col.side")}</Th>
                <Th right>{t("col.qty")}</Th>
                <Th right>{t("col.fill")}</Th>
                <Th right>{t("col.closedPnl")}</Th>
                <Th>{t("col.outcome")}</Th>
                <Th>{t("col.reason")}</Th>
              </TableHeadRow>
            </thead>
            <tbody>
              {pageItems.map((d) => {
                const blocked = d.outcome === "risk_rejected";
                // reason 回退:拒单有真实 reason;成交(reason 为空)退到策略 tag,
                // 再退到「策略信号成交」——让该列对正常成交行也有信息,不再一片「—」。
                const reasonText =
                  d.reason ??
                  d.tag ??
                  (d.outcome === "filled" ? t("reasonFilled") : null);
                return (
                  <tr
                    key={d.id}
                    className={cn(
                      "border-t border-border-subtle/60",
                      blocked
                        ? "bg-fox-red/[0.06] hover:bg-fox-red/[0.1]"
                        : "hover:bg-bg-elev/30",
                    )}
                  >
                    <Td mono muted className="whitespace-nowrap">
                      {fmtBarTs(d.bar_ts, locale)}
                    </Td>
                    <Td right mono muted>
                      {fmtNum(d.bar_close, locale, 4)}
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
                    <Td right mono>
                      {d.closed_profit_abs === null ? (
                        <span className="text-fg-muted/50">—</span>
                      ) : (
                        <span className={pnlColor(d.closed_profit_abs)}>
                          {fmtSigned(d.closed_profit_abs, null, locale)}
                          {d.closed_profit_pct !== null && (
                            <span className="ml-1 text-[10px]">
                              ({d.closed_profit_pct >= 0 ? "+" : ""}
                              {d.closed_profit_pct.toFixed(2)}%)
                            </span>
                          )}
                        </span>
                      )}
                    </Td>
                    <Td>
                      <DecisionOutcomeBadge
                        outcome={d.outcome}
                        label={tOutcome(d.outcome)}
                      />
                    </Td>
                    <Td>
                      {reasonText ? (
                        <span
                          title={reasonText}
                          className={cn(
                            // 单行不换行,过长截断省略,悬浮 title 看全文。
                            "block max-w-[22rem] truncate font-mono text-[11px]",
                            blocked ? "text-fox-red" : "text-fg-muted",
                          )}
                        >
                          {reasonText}
                        </span>
                      ) : (
                        <span className="text-fg-muted/40">—</span>
                      )}
                    </Td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <Pager page={page} pageCount={pageCount} onChange={setPage} />
        </div>
      )}
    </Panel>
  );
}

/** bar 时间显示到分钟(决策时间线跨多根 bar,需带日期)。 */
function fmtBarTs(iso: string, locale: string): string {
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
