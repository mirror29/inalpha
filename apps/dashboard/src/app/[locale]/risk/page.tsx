import { setRequestLocale } from "next-intl/server";

import { RiskClient } from "@/components/risk/RiskClient";

/** ⑤ 风控面板 —— 规则配置 + 当前活跃锁。 */
export default async function RiskPage({
  params,
}: {
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  setRequestLocale(locale);
  return <RiskClient />;
}
