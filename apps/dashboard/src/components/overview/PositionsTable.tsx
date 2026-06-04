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
    <Panel
      index="1.1"
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
        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-sm">
            <thead>
              <TableHeadRow>
                <Th>{t("col.instrument")}</Th>
                <Th right>{t("col.qty")}</Th>
                <Th right>{t("col.avgPrice")}</Th>
                <Th right>{t("col.mark")}</Th>
                <Th right>{t("col.unrealized")}</Th>
                <Th right>{t("col.realized")}</Th>
              </TableHeadRow>
            </thead>
            <tbody>
              {positions.map((p) => {
                const ccy = p.currency ?? baseCcy;
                return (
                  <tr
                    key={`${p.venue}:${p.symbol}`}
                    className="border-t border-border-subtle/60 hover:bg-bg-elev/30"
                  >
                    <Td>
                      <span className="font-medium text-fg">
                        {instrumentLabel(p.symbol, p.venue)}
                      </span>
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
