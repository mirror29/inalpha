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
    <div className="flex flex-col gap-6">
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

function OverviewSkeleton() {
  return (
    <div className="flex flex-col gap-6">
      <SkeletonBlock className="h-16 w-72 border-0 bg-bg-elev/40" />
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {Array.from({ length: 4 }).map((_, i) => (
          <SkeletonBlock key={i} className="h-24" />
        ))}
      </div>
      <div className="grid grid-cols-1 gap-6 xl:grid-cols-[1.4fr_1fr]">
        <SkeletonBlock className="h-64" />
        <SkeletonBlock className="h-64" />
      </div>
    </div>
  );
}
