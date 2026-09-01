import { setRequestLocale } from "next-intl/server";

import { DataHealthClient } from "@/components/data-health/DataHealthClient";

/** Event source coverage and point-in-time ledger health. */
export default async function DataHealthPage({ params }: { params: Promise<{ locale: string }> }) {
  const { locale } = await params;
  setRequestLocale(locale);
  return <DataHealthClient />;
}
