import { setRequestLocale } from "next-intl/server";

import { EvolutionClient } from "@/components/evolution/EvolutionClient";

/** 策略演化监控 —— LLM 驱动的策略自动变异与评估。 */
export default async function EvolutionPage({
  params,
}: {
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  setRequestLocale(locale);
  return <EvolutionClient />;
}