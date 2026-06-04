import { setRequestLocale } from "next-intl/server";

import { FactorsClient } from "@/components/factors/FactorsClient";

/** ⑥ 因子库 —— 因子目录 + 当前标的有效因子择时(Rank IC)。 */
export default async function FactorsPage({
  params,
}: {
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  setRequestLocale(locale);
  return <FactorsClient />;
}
