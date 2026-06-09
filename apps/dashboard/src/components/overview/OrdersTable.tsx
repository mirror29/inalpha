"use client";

import { useLocale, useTranslations } from "next-intl";

import type { OrderRecord } from "@/lib/types";
import { cn } from "@/lib/cn";
import {
  fmtNum,
  fmtQty,
  fmtSigned,
  fmtTime,
  instrumentLabel,
  pnlColor,
} from "@/lib/format";
import { Panel } from "@/components/ui/Panel";
import { OrderStatusBadge } from "@/components/ui/StatusBadge";
import { Td, TableEmpty, TableHeadRow, Th } from "@/components/ui/Table";
import { TruncationNote } from "@/components/ui/TruncationNote";

export function OrdersTable({
  orders,
  truncated = false,
}: {
  orders: OrderRecord[];
  truncated?: boolean;
}) {
  const t = useTranslations("overview.orders");
  const tc = useTranslations("common");
  const locale = useLocale();

  return (
    <Panel
      title={t("title")}
      aside={
        <span className="tnum font-mono text-xs text-fg-muted">
          {orders.length}
        </span>
      }
    >
      {orders.length === 0 ? (
        <TableEmpty>{t("empty")}</TableEmpty>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-sm">
            <thead>
              <TableHeadRow>
                <Th>{t("col.time")}</Th>
                <Th>{t("col.instrument")}</Th>
                <Th>{t("col.side")}</Th>
                <Th>{t("col.type")}</Th>
                <Th right>{t("col.qty")}</Th>
                <Th right>{t("col.fill")}</Th>
                <Th right>{t("col.pnl")}</Th>
                <Th right>{t("col.status")}</Th>
              </TableHeadRow>
            </thead>
            <tbody>
              {orders.map((o) => (
                <tr
                  key={o.client_order_id}
                  className="border-t border-border-subtle/60 hover:bg-bg-elev/30"
                >
                  <Td mono muted>
                    {fmtTime(o.ts_event, locale)}
                  </Td>
                  <Td>
                    <span className="font-medium text-fg">
                      {instrumentLabel(o.symbol, o.venue)}
                    </span>
                  </Td>
                  <Td>
                    <span
                      className={cn(
                        "font-mono text-xs font-medium uppercase",
                        o.side === "BUY" ? "text-bull" : "text-fox-red",
                      )}
                    >
                      {o.side}
                    </span>
                  </Td>
                  <Td mono muted>
                    {o.type}
                  </Td>
                  <Td right mono>
                    {fmtQty(o.quantity, locale)}
                  </Td>
                  <Td right mono muted>
                    {o.avg_fill_price === null
                      ? "—"
                      : fmtNum(o.avg_fill_price, locale, 4)}
                  </Td>
                  <Td right mono>
                    {/* == null 同时挡 null 与 undefined(旧 paper 服务未升级前不返该字段)。 */}
                    {o.realized_pnl == null ? (
                      <span className="text-fg-muted/40">—</span>
                    ) : o.realized_pnl === 0 ? (
                      <span className="text-fg-muted/60">0</span>
                    ) : (
                      <span className={pnlColor(o.realized_pnl)}>
                        {fmtSigned(o.realized_pnl, null, locale)}
                      </span>
                    )}
                  </Td>
                  <Td right>
                    <OrderStatusBadge status={o.status} />
                  </Td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {truncated && (
        <div className="px-4 pb-3 pt-2">
          <TruncationNote text={tc("truncated", { n: orders.length })} />
        </div>
      )}
    </Panel>
  );
}
