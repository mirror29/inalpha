import { setRequestLocale } from "next-intl/server";

import { ActivityClient } from "@/components/activity/ActivityClient";

/** ③ Agent 运行日志 / 可观测性 —— 跨模块活动统一时间线。 */
export default async function ActivityPage({
  params,
}: {
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  setRequestLocale(locale);
  return <ActivityClient />;
}
