import { setRequestLocale } from "next-intl/server";

import { RunnerDetailClient } from "@/components/runners/RunnerDetailClient";

/** 单个 run 的决策复盘详情。 */
export default async function RunnerDetailPage({
  params,
}: {
  params: Promise<{ locale: string; id: string }>;
}) {
  const { locale, id } = await params;
  setRequestLocale(locale);
  return <RunnerDetailClient runId={id} />;
}
