import { setRequestLocale } from "next-intl/server";

import { RunnersClient } from "@/components/runners/RunnersClient";

/** ② Live Runner 监控 —— promoted 策略按行情自动跑的运行态。 */
export default async function RunnersPage({
  params,
}: {
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  setRequestLocale(locale);
  return <RunnersClient />;
}
