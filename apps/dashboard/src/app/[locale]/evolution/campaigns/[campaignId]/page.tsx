import { setRequestLocale } from "next-intl/server";

import { EvolutionCampaignDetailClient } from "@/components/evolution/EvolutionCampaignDetailClient";

/** Event-driven multi-generation campaign workbench. */
export default async function EvolutionCampaignPage({ params }: { params: Promise<{ locale: string; campaignId: string }> }) {
  const { locale, campaignId } = await params;
  setRequestLocale(locale);
  return <EvolutionCampaignDetailClient campaignId={campaignId} />;
}
