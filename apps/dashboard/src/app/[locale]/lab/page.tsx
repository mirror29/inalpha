import { setRequestLocale } from "next-intl/server";

import { LabClient } from "@/components/lab/LabClient";

/** ④ 策略实验室 —— LLM 自创策略候选(按 fitness 排序)+ 回测指标。 */
export default async function LabPage({
  params,
}: {
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  setRequestLocale(locale);
  return <LabClient />;
}
