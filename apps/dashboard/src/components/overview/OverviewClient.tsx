"use client";

import { useTranslations } from "next-intl";
import useSWR from "swr";

import type { OverviewPayload } from "@/lib/types";
import { jsonFetcher } from "@/lib/fetcher";
import { ErrorState, SkeletonBlock } from "@/components/ui/Feedback";
import { LiveStrip, Meta } from "@/components/ui/LiveStrip";
import { PageHeader } from "@/components/ui/PageHeader";
import { FxWarningBanner } from "./FxWarningBanner";
import { KpiBar } from "./KpiBar";
import { OrdersTable } from "./OrdersTable";
import { PositionsTable } from "./PositionsTable";
import { RunnersPanel } from "./RunnersPanel";
import { StrategyPanel } from "./StrategyPanel";

/** 账户/持仓/订单变化较慢,8s 一档(见设计文档轮询节奏)。 */
const REFRESH_MS = 8000;

export function OverviewClient() {
  const t = useTranslations("overview");
  const tStatus = useTranslations("status");

  const { data, error, isLoading, isValidating, mutate } =
    useSWR<OverviewPayload>("/api/overview", jsonFetcher, {
      refreshInterval: REFRESH_MS,
      keepPreviousData: true, // 刷新失败/进行中时保留上一帧,不闪烁
    });

  if (isLoading && !data) {
    return <OverviewSkeleton />;
  }

  if (error && !data) {
    return (
      <ErrorState
        message={error instanceof Error ? error.message : String(error)}
        onRetry={() => mutate()}
      />
    );
  }

  if (!data) return null;

  return (
    // @container:总览各栅格按「主内容实际可用宽度」自适应,而非视口宽 —— 对话栏展开后
    // main 被挤窄(padding-right: --chat-w),视口断点会误判仍宽 → KPI 数字被截断。
    <div className="@container flex flex-col gap-6">
      <PageHeader
        title={t("title")}
        subtitle={t("subtitle")}
        right={
          <LiveStrip
            asOf={data.asOf}
            isValidating={isValidating}
            isStaleFrame={Boolean(error)}
          >
            <Meta
              label={tStatus("account")}
              value={data.account.account_id.slice(0, 8)}
            />
            <Meta label={tStatus("base")} value={data.account.base_currency} />
          </LiveStrip>
        }
      />

      <FxWarningBanner warnings={data.account.fx_warnings} />

      <KpiBar data={data} />

      {/* 系统在做什么:运行中的 live runner + 策略池,并排成「执行 / 研究」两栏。 */}
      <div className="grid grid-cols-1 gap-6 @4xl:grid-cols-2">
        <RunnersPanel runs={data.runs} />
        <StrategyPanel
          candidates={data.candidates}
          counts={data.candidateCounts}
        />
      </div>

      <div className="grid grid-cols-1 gap-6 @4xl:grid-cols-[1.4fr_1fr]">
        <PositionsTable
          positions={data.positions}
          baseCcy={data.account.base_currency}
        />
        <OrdersTable orders={data.orders} truncated={data.ordersTruncated} />
      </div>
    </div>
  );
}

function OverviewSkeleton() {
  return (
    <div className="@container flex flex-col gap-6">
      <SkeletonBlock className="h-16 w-72 border-0 bg-bg-elev/40" />
      <div className="grid grid-cols-1 gap-3 @md:grid-cols-2 @5xl:grid-cols-3">
        {Array.from({ length: 6 }).map((_, i) => (
          <SkeletonBlock key={i} className="h-24" />
        ))}
      </div>
      <div className="grid grid-cols-1 gap-6 @4xl:grid-cols-2">
        <SkeletonBlock className="h-44" />
        <SkeletonBlock className="h-44" />
      </div>
      <div className="grid grid-cols-1 gap-6 @4xl:grid-cols-[1.4fr_1fr]">
        <SkeletonBlock className="h-64" />
        <SkeletonBlock className="h-64" />
      </div>
    </div>
  );
}
