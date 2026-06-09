"use client";

import { useLocale, useTranslations } from "next-intl";
import { motion, useReducedMotion } from "motion/react";

import type { OverviewPayload } from "@/lib/types";
import { cn } from "@/lib/cn";
import { fmtMoney, fmtSigned, pnlColor } from "@/lib/format";

/** 收益率(已带 +/− 号),2 位小数;不可计算(初始资金为 0)时返回 null。 */
function fmtReturnPct(net: number, initial: number, locale: string): string | null {
  if (!initial) return null;
  const pct = (net / initial) * 100;
  const sign = pct > 0 ? "+" : pct < 0 ? "−" : "";
  return `${sign}${new Intl.NumberFormat(locale, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(Math.abs(pct))}%`;
}

/**
 * 顶部 KPI 条 —— 总控制台看板的「一眼总览」行:
 * 总权益 / 总收益率 / 现金(多币种)/ 浮动盈亏 / 已实现盈亏 / 运行中策略数。
 * 大号等宽数字,工程仪表盘观感。
 */
export function KpiBar({ data }: { data: OverviewPayload }) {
  const t = useTranslations("overview.kpi");
  const locale = useLocale();
  const { account, activeRunnerCount, runs, positions } = data;
  const ccy = account.base_currency;

  // 浮动盈亏聚合:仅累加拿到最新价的持仓;有持仓缺价时标 partial(不静默低估)。
  let unrealizedSum = 0;
  let unrealizedPartial = false;
  for (const p of positions) {
    if (p.unrealized_pnl === null) unrealizedPartial = true;
    else unrealizedSum += p.unrealized_pnl;
  }

  // 总收益率 / 累计净盈亏(权益相对起始资金)。
  const netPnl = account.total_equity - account.initial_cash;
  const returnPct = fmtReturnPct(netPnl, account.initial_cash, locale);

  return (
    <div className="grid grid-cols-1 gap-3 @md:grid-cols-2 @5xl:grid-cols-3">
      <KpiCard label={t("totalEquity")} accent="cyan" i={0}>
        <Figure>{fmtMoney(account.total_equity, ccy, locale)}</Figure>
        <Sub>
          {t("positionsValue")} {fmtMoney(account.positions_value, ccy, locale)}
        </Sub>
      </KpiCard>

      <KpiCard label={t("totalReturn")} accent="cyan" i={1}>
        <Figure className={pnlColor(netPnl)}>{returnPct ?? "—"}</Figure>
        <Sub>
          {t("netSinceInception", { pnl: fmtSigned(netPnl, ccy, locale) })}
        </Sub>
      </KpiCard>

      <KpiCard label={t("cash")} i={2}>
        <Figure>{fmtMoney(account.cash, ccy, locale)}</Figure>
        <CashBuckets balances={account.cash_balances} base={ccy} />
      </KpiCard>

      <KpiCard label={t("unrealizedPnl")} i={3}>
        <Figure className={pnlColor(unrealizedSum)}>
          {fmtSigned(unrealizedSum, ccy, locale)}
        </Figure>
        <Sub>
          {unrealizedPartial
            ? t("partialMark")
            : t("openPositions", { count: positions.length })}
        </Sub>
      </KpiCard>

      <KpiCard label={t("realizedPnl")} i={4}>
        <Figure className={pnlColor(account.realized_pnl)}>
          {fmtSigned(account.realized_pnl, ccy, locale)}
        </Figure>
        <Sub>{t("convertedTo", { ccy })}</Sub>
      </KpiCard>

      <KpiCard label={t("activeRunners")} accent="bull" i={5}>
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
  accent = "seal",
  i = 0,
  children,
}: {
  label: string;
  accent?: "cyan" | "bull" | "seal";
  /** 错落入场用的序号。 */
  i?: number;
  children: React.ReactNode;
}) {
  const reduce = useReducedMotion();
  return (
    <motion.div
      initial={reduce ? false : { opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: i * 0.07, duration: 0.45, ease: [0.22, 0.7, 0.22, 1] }}
      whileHover={reduce ? undefined : { y: -3 }}
      className="group relative overflow-hidden rounded-xl border border-border-subtle bg-bg-elev/40 px-4 py-3.5 backdrop-blur-sm transition-[border-color,box-shadow] hover:border-cyan/40 hover:shadow-[0_10px_30px_-14px_rgba(0,0,0,0.55)]"
    >
      {/* 顶部 1px 标尺,按指标语义着色 —— 终端「通道灯」语感。 */}
      <span
        className={cn(
          "absolute inset-x-0 top-0 h-px",
          accent === "cyan"
            ? "bg-cyan/60"
            : accent === "bull"
              ? "bg-bull/60"
              : "bg-seal/50",
        )}
      />
      <div className="tick-accent whitespace-nowrap pl-2.5 font-mono text-[10px] uppercase tracking-[0.16em] text-fg-muted">
        {label}
      </div>
      <div className="mt-2.5 pl-2.5">{children}</div>
    </motion.div>
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
        // 总控制台一行 3 张卡,数字给足横向空间;truncate 防超长币值溢出裁切。
        "tnum truncate font-mono text-2xl leading-none tracking-tight text-fg lg:text-[1.75rem]",
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
