import { setRequestLocale } from "next-intl/server";

import { DivinationClient } from "@/components/divination/DivinationClient";

/**
 * 占卜台页面(纯娱乐彩蛋)。
 *
 * server component 负责 i18n 定位,交互逻辑在 DivinationClient(client)。
 */
export default async function DivinationPage({
  params,
}: {
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  setRequestLocale(locale);
  return <DivinationClient />;
}
