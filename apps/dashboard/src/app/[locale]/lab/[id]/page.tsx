import { setRequestLocale } from "next-intl/server";

import { CandidateDetailClient } from "@/components/lab/CandidateDetailClient";

/** 单个候选详情 —— 指标 + 审计 + 源码。 */
export default async function CandidateDetailPage({
  params,
}: {
  params: Promise<{ locale: string; id: string }>;
}) {
  const { locale, id } = await params;
  setRequestLocale(locale);
  return <CandidateDetailClient id={id} />;
}
