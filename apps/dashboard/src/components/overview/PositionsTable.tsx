"use client";

import { useLocale, useTranslations } from "next-intl";

import type { PositionWithMark } from "@/lib/types";
import { cn } from "@/lib/cn";
import {
  fmtNum,
  fmtQty,
  fmtSigned,
  instrumentLabel,
  pnlColor,
} from "@/lib/format";
import { Panel } from "@/components/ui/Panel";
import { Td, TableEmpty, TableHeadRow, Th } from "@/components/ui/Table";

export function PositionsTable({
  positions,
  baseCcy,
}: {
  positions: PositionWithMark[];
  baseCcy: string;
}) {
  const t = useTranslations("overview.positions");
  const tStatus = useTranslations("status");
  const locale = useLocale();

  return (
    // h-full:总览里与 runner 面板并排,grid stretch 下两卡等高;
    // RunnerDetail 复用处父容器 items-start(不拉伸),h-full 为 no-op,不影响。
    <Panel
      className="h-full"
      title={t("title")}
      aside={
        <span className="tnum font-mono text-xs text-fg-muted">
          {positions.length}
        </span>
      }
    >
      {positions.length === 0 ? (
        <TableEmpty>{t("empty")}</TableEmpty>
      ) : (
        // 限高内滚+表头吸顶(同 DecisionTimeline 模式):持仓多时不撑爆同排的
        // runner 卡;RunnerDetail 复用处只有一行,限高不触发。
        <div className="max-h-96 overflow-x-auto overflow-y-auto">
          <table className="w-full border-collapse text-sm">
            <thead className="sticky top-0 z-10 bg-bg-elev">
              <TableHeadRow>
                <Th>{t("col.instrument")}</Th>
                <Th right>{t("col.qty")}</Th>
                <Th right>{t("col.avgPrice")}</Th>
                <Th right>{t("col.mark")}</Th>
                <Th right>{t("col.margin")}</Th>
                <Th right>{t("col.liqPrice")}</Th>
                <Th right>{t("col.unrealized")}</Th>
                <Th right>{t("col.realized")}</Th>
              </TableHeadRow>
            </thead>
            <tbody>
              {positions.map((p) => {
                const ccy = p.currency ?? baseCcy;
                // 后端派生的显式模式;perp 杠杆徽标恒显示(含 1×),现货/合约一眼可辨。
                const isPerp = p.trading_mode === "perp";
                return (
                  <tr
                    key={`${p.venue}:${p.symbol}`}
                    className="border-t border-border-subtle/60 hover:bg-bg-elev/30"
                  >
                    <Td>
                      <span className="font-medium text-fg">
                        {instrumentLabel(p.symbol, p.venue)}
                      </span>
                      <span
                        className={cn(
                          "ml-1.5 rounded px-1 py-0.5 font-mono text-[10px]",
                          isPerp ? "bg-gold/10 text-gold" : "bg-bg-elev text-fg-muted",
                        )}
                      >
                        {isPerp ? t("modePerp") : t("modeSpot")}
                      </span>
                      {isPerp && (
                        <span
                          className="ml-1 rounded bg-bg-elev px-1 py-0.5 font-mono text-[10px] text-gold"
                          title={t("leverageTitle", {
                            leverage: p.leverage,
                            margin: fmtSigned(p.margin_used, ccy, locale),
                          })}
                        >
                          {p.leverage}×
                        </span>
                      )}
                    </Td>
                    <Td right mono>
                      <span className={p.quantity < 0 ? "text-fox-red" : "text-fg"}>
                        {fmtQty(p.quantity, locale)}
                      </span>
                    </Td>
                    <Td right mono muted>
                      {fmtNum(p.avg_open_price, locale, 4)}
                    </Td>
                    <Td right mono>
                      {p.mark_price === null ? (
                        <span className="text-fg-muted/50">—</span>
                      ) : (
                        <span
                          className={cn(
                            "inline-flex items-center gap-1",
                            p.mark_stale ? "text-gold" : "text-fg",
                          )}
                        >
                          {fmtNum(p.mark_price, locale, 4)}
                          {p.mark_stale && (
                            <span
                              className="inline-block size-1.5 rounded-full bg-gold"
                              title={tStatus("stale")}
                            />
                          )}
                        </span>
                      )}
                    </Td>
                    <Td right mono>
                      {!isPerp ? (
                        <span className="text-fg-muted/50">—</span>
                      ) : (
                        <span className="text-fg" title={t("marginTitle")}>
                          {fmtNum(p.margin_used, locale, 2)}
                        </span>
                      )}
                    </Td>
                    <Td right mono>
                      {!isPerp || p.liquidation_price === null ? (
                        <span className="text-fg-muted/50">—</span>
                      ) : (
                        <span className="text-fox-red" title={t("liqStale")}>
                          {fmtNum(p.liquidation_price, locale, 4)}
                        </span>
                      )}
                    </Td>
                    <Td right mono>
                      {p.unrealized_pnl === null ? (
                        <span className="text-fg-muted/50">—</span>
                      ) : (
                        <span className={pnlColor(p.unrealized_pnl)}>
                          {fmtSigned(p.unrealized_pnl, ccy, locale)}
                        </span>
                      )}
                    </Td>
                    <Td right mono>
                      <span className={pnlColor(p.realized_pnl)}>
                        {fmtSigned(p.realized_pnl, ccy, locale)}
                      </span>
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
