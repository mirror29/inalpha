import { setRequestLocale } from "next-intl/server";

import { EvolutionLoopDetailClient } from "@/components/evolution/EvolutionLoopDetailClient";

/** Keep one durable detail URL before and after E2 exists. */
export default async function EvolutionLoopPage({ params }: { params: Promise<{ locale: string; loopId: string }> }) {
  const { locale, loopId } = await params;
  setRequestLocale(locale);
  return <EvolutionLoopDetailClient loopId={loopId} />;
}
