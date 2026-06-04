"use client";

import { useLocale, useNow, useTranslations } from "next-intl";
import { RotateCw, WifiOff } from "lucide-react";
import useSWR from "swr";

import type { OverviewPayload } from "@/lib/types";
import { cn } from "@/lib/cn";
import { fmtRelative, fmtTime } from "@/lib/format";
import { jsonFetcher } from "@/lib/fetcher";
import { FxWarningBanner } from "./FxWarningBanner";
import { KpiBar } from "./KpiBar";
import { OrdersTable } from "./OrdersTable";
import { PositionsTable } from "./PositionsTable";

/** 账户/持仓/订单变化较慢,8s 一档(见设计文档轮询节奏)。 */
const REFRESH_MS = 8000;

export function OverviewClient() {
  const t = useTranslations("overview");
  const tCommon = useTranslations("common");
  const tStatus = useTranslations("status");

  const { data, error, isLoading, isValidating, mutate } =
    useSWR<OverviewPayload>("/api/overview", jsonFetcher, {
      refreshInterval: REFRESH_MS,
      keepPreviousData: true, // 刷新失败/进行中时保留上一帧,不闪烁
    });

  // 首屏加载(还没有任何帧)。
  if (isLoading && !data) {
    return <OverviewSkeleton />;
  }

  // 彻底拿不到数据(连一帧都没有)→ 错误态 + 重试。
  if (error && !data) {
    return (
      <div className="flex flex-col items-center justify-center gap-4 py-24 text-center">
        <WifiOff className="size-8 text-fox-red" strokeWidth={1.5} />
        <div>
          <div className="font-mono text-sm text-fg">{tCommon("error")}</div>
          <div className="mt-1 font-mono text-xs text-fg-muted">
            {error instanceof Error ? error.message : String(error)}
          </div>
        </div>
        <button
          type="button"
          onClick={() => mutate()}
          className="inline-flex items-center gap-2 rounded-md border border-border-subtle px-4 py-2 font-mono text-xs text-fg transition-colors hover:border-cyan hover:text-cyan"
        >
          <RotateCw className="size-3.5" />
          {tCommon("retry")}
        </button>
      </div>
    );
  }

  if (!data) return null;

  // 有数据但最近一次刷新失败 → 顶部显示「后端离线 · 显示上一帧」。
  const isStaleFrame = Boolean(error);

  return (
    <div className="flex flex-col gap-6">
      <Header
        title={t("title")}
        index={t("index")}
        subtitle={t("subtitle")}
        accountId={data.account.account_id}
        baseCcy={data.account.base_currency}
        asOf={data.asOf}
        isValidating={isValidating}
        isStaleFrame={isStaleFrame}
        labels={{
          live: tStatus("live"),
          account: tStatus("account"),
          asOf: tStatus("asOf"),
          base: tStatus("base"),
          offline: tStatus("offline"),
          lastFrame: tStatus("lastFrame"),
        }}
      />

      <FxWarningBanner warnings={data.account.fx_warnings} />

      <KpiBar data={data} />

      <div className="grid grid-cols-1 gap-6 xl:grid-cols-[1.4fr_1fr]">
        <PositionsTable
          positions={data.positions}
          baseCcy={data.account.base_currency}
        />
        <OrdersTable orders={data.orders} />
      </div>
    </div>
  );
}

interface HeaderProps {
  title: string;
  index: string;
  subtitle: string;
  accountId: string;
  baseCcy: string;
  asOf: string;
  isValidating: boolean;
  isStaleFrame: boolean;
  labels: {
    live: string;
    account: string;
    asOf: string;
    base: string;
    offline: string;
    lastFrame: string;
  };
}

function Header({
  title,
  index,
  subtitle,
  accountId,
  baseCcy,
  asOf,
  isValidating,
  isStaleFrame,
  labels,
}: HeaderProps) {
  const locale = useLocale();
  // 每 10s 自动重算相对时间,让 "X 前" 走动。
  const now = useNow({ updateInterval: 10_000 });

  return (
    <header className="flex flex-col gap-4 border-b border-border-subtle pb-5 lg:flex-row lg:items-end lg:justify-between">
      <div>
        <div className="flex items-baseline gap-3">
          <span className="font-display text-lg italic text-fg-muted/70">
            {index}
          </span>
          <h1 className="font-display text-3xl text-fg lg:text-4xl">{title}</h1>
        </div>
        <p className="mt-2 max-w-xl text-sm text-fg-muted">{subtitle}</p>
      </div>

      {/* 状态条 */}
      <div className="flex flex-wrap items-center gap-x-5 gap-y-2 font-mono text-[11px]">
        {isStaleFrame ? (
          <Meta
            label={labels.offline}
            tone="fox"
            value={labels.lastFrame}
            dot
          />
        ) : (
          <Meta
            label={labels.live}
            tone="bull"
            value={`${fmtTime(asOf, locale)} · ${fmtRelative(
              asOf,
              now.getTime(),
              locale,
            )}`}
            dot
            pulse={isValidating}
          />
        )}
        <Meta label={labels.account} value={accountId.slice(0, 8)} />
        <Meta label={labels.base} value={baseCcy} />
      </div>
    </header>
  );
}

function Meta({
  label,
  value,
  tone = "muted",
  dot = false,
  pulse = false,
}: {
  label: string;
  value: string;
  tone?: "bull" | "fox" | "muted";
  dot?: boolean;
  pulse?: boolean;
}) {
  const toneText =
    tone === "bull" ? "text-bull" : tone === "fox" ? "text-fox-red" : "text-fg";
  return (
    <div className="flex items-center gap-2">
      <span className="uppercase tracking-[0.16em] text-fg-muted/60">
        {label}
      </span>
      <span className={cn("flex items-center gap-1.5 tabular-nums", toneText)}>
        {dot && (
          <span className="relative flex size-1.5">
            {pulse && (
              <span className="absolute inline-flex size-full animate-ping rounded-full bg-current opacity-60" />
            )}
            <span className="relative inline-flex size-1.5 rounded-full bg-current" />
          </span>
        )}
        {value}
      </span>
    </div>
  );
}

function OverviewSkeleton() {
  return (
    <div className="flex flex-col gap-6">
      <div className="h-16 w-72 animate-pulse rounded-lg bg-bg-elev/40" />
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {Array.from({ length: 4 }).map((_, i) => (
          <div
            key={i}
            className="h-24 animate-pulse rounded-xl border border-border-subtle bg-bg-elev/30"
          />
        ))}
      </div>
      <div className="grid grid-cols-1 gap-6 xl:grid-cols-[1.4fr_1fr]">
        <div className="h-64 animate-pulse rounded-xl border border-border-subtle bg-bg-elev/30" />
        <div className="h-64 animate-pulse rounded-xl border border-border-subtle bg-bg-elev/30" />
      </div>
    </div>
  );
}
