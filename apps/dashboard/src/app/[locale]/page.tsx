import { setRequestLocale } from "next-intl/server";

import { OverviewClient } from "@/components/overview/OverviewClient";

/**
 * ① 组合总览 —— 控制台落地页。
 * 数据在客户端轮询(SWR → /api/overview),页面本身是壳。
 */
export default async function OverviewPage({
  params,
}: {
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  setRequestLocale(locale);
  return <OverviewClient />;
}
