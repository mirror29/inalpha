import { setRequestLocale } from "next-intl/server";

import { BacktestRunDetailClient } from "@/components/backtests/BacktestRunDetailClient";

/** 单次回测详情 —— 指标 + 区间 K 线 + 逐笔成交(活动流点击回测事件的落地页)。 */
export default async function BacktestRunDetailPage({
  params,
}: {
  params: Promise<{ locale: string; runId: string }>;
}) {
  const { locale, runId } = await params;
  setRequestLocale(locale);
  return <BacktestRunDetailClient runId={runId} />;
}
