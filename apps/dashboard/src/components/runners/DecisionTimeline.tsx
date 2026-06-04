"use client";

import { useLocale, useTranslations } from "next-intl";

import type { StrategyRunDecisionRecord } from "@/lib/types";
import { cn } from "@/lib/cn";
import { fmtNum, fmtQty } from "@/lib/format";
import { Panel } from "@/components/ui/Panel";
import { DecisionOutcomeBadge } from "@/components/ui/StatusBadge";
import { Td, TableEmpty, TableHeadRow, Th } from "@/components/ui/Table";

/**
 * 决策复盘时间线 —— 每根产生下单意图的 bar 一行。
 * risk_rejected 行整行染红底,reason 直接展示风控拒单理由(这是用户最常问 agent 的
 * "那一单为什么没成")。
 */
export function DecisionTimeline({
  decisions,
}: {
  decisions: StrategyRunDecisionRecord[];
}) {
  const t = useTranslations("runners.detail");
  const tIntent = useTranslations("runners.intent");
  const tOutcome = useTranslations("runners.outcome");
  const locale = useLocale();

  return (
    <Panel
      index="2.1"
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
        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-sm">
            <thead>
              <TableHeadRow>
                <Th>{t("col.barTs")}</Th>
                <Th right>{t("col.barClose")}</Th>
                <Th>{t("col.intent")}</Th>
                <Th>{t("col.side")}</Th>
                <Th right>{t("col.qty")}</Th>
                <Th right>{t("col.fill")}</Th>
                <Th>{t("col.outcome")}</Th>
                <Th>{t("col.reason")}</Th>
              </TableHeadRow>
            </thead>
            <tbody>
              {decisions.map((d) => {
                const blocked = d.outcome === "risk_rejected";
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
                    <Td mono muted>
                      {fmtBarTs(d.bar_ts, locale)}
                    </Td>
                    <Td right mono muted>
                      {fmtNum(d.bar_close, locale, 4)}
                    </Td>
                    <Td>
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
                    <Td>
                      <DecisionOutcomeBadge
                        outcome={d.outcome}
                        label={tOutcome(d.outcome)}
                      />
                    </Td>
                    <Td>
                      {d.reason ? (
                        <span
                          className={cn(
                            "font-mono text-[11px]",
                            blocked ? "text-fox-red" : "text-fg-muted",
                          )}
                        >
                          {d.reason}
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
