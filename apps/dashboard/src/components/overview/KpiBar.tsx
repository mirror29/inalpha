"use client";

import { useLocale, useTranslations } from "next-intl";

import type { OverviewPayload } from "@/lib/types";
import { cn } from "@/lib/cn";
import { fmtMoney, fmtSigned, pnlColor } from "@/lib/format";

/**
 * 顶部 KPI 条:总权益 / 现金(多币种)/ 已实现盈亏 / 运行中策略数。
 * 大号等宽数字,工程仪表盘观感。
 */
export function KpiBar({ data }: { data: OverviewPayload }) {
  const t = useTranslations("overview.kpi");
  const locale = useLocale();
  const { account, activeRunnerCount, runs } = data;
  const ccy = account.base_currency;

  return (
    <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
      <KpiCard label={t("totalEquity")} accent="cyan">
        <Figure>{fmtMoney(account.total_equity, ccy, locale)}</Figure>
        <Sub>
          {t("positionsValue")} {fmtMoney(account.positions_value, ccy, locale)}
        </Sub>
      </KpiCard>

      <KpiCard label={t("cash")}>
        <Figure>{fmtMoney(account.cash, ccy, locale)}</Figure>
        <CashBuckets balances={account.cash_balances} base={ccy} />
      </KpiCard>

      <KpiCard label={t("realizedPnl")}>
        <Figure className={pnlColor(account.realized_pnl)}>
          {fmtSigned(account.realized_pnl, ccy, locale)}
        </Figure>
        <Sub>{t("convertedTo", { ccy })}</Sub>
      </KpiCard>

      <KpiCard label={t("activeRunners")} accent="bull">
        <Figure className={activeRunnerCount > 0 ? "text-bull" : undefined}>
          {activeRunnerCount}
        </Figure>
        <Sub>{t("ofRuns", { total: runs.length })}</Sub>
      </KpiCard>
    </div>
  );
}

function KpiCard({
  label,
  accent,
  children,
}: {
  label: string;
  accent?: "cyan" | "bull";
  children: React.ReactNode;
}) {
  return (
    <div className="relative overflow-hidden rounded-xl border border-border-subtle bg-bg-elev/30 px-4 py-3.5 backdrop-blur-sm">
      {accent && (
        <span
          className={cn(
            "absolute inset-x-0 top-0 h-px",
            accent === "cyan" ? "bg-cyan/50" : "bg-bull/50",
          )}
        />
      )}
      <div className="font-mono text-[10px] uppercase tracking-[0.16em] text-fg-muted">
        {label}
      </div>
      <div className="mt-2">{children}</div>
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
        "tnum font-mono text-2xl leading-none tracking-tight text-fg lg:text-[1.75rem]",
        className,
      )}
    >
      {children}
    </div>
  );
}

function Sub({ children }: { children: React.ReactNode }) {
  return (
    <div className="tnum mt-1.5 font-mono text-[11px] text-fg-muted/80">
      {children}
    </div>
  );
}

/** 多币种现金桶 chips —— 折算前的原始按币种余额。 */
function CashBuckets({
  balances,
  base,
}: {
  balances: Record<string, number>;
  base: string;
}) {
  const locale = useLocale();
  const entries = Object.entries(balances).filter(([, v]) => v !== 0);
  if (entries.length <= 1) {
    return <Sub>{base}</Sub>;
  }
  return (
    <div className="mt-1.5 flex flex-wrap gap-1">
      {entries.map(([code, amt]) => (
        <span
          key={code}
          className="tnum rounded border border-border-subtle/70 bg-bg-deep/40 px-1.5 py-0.5 font-mono text-[10px] text-fg-muted"
          title={code}
        >
          <span className="text-fg-muted/60">{code}</span>{" "}
          <span className={amt < 0 ? "text-fox-red" : "text-fg"}>
            {fmtMoney(amt, code, locale)}
          </span>
        </span>
      ))}
    </div>
  );
}
