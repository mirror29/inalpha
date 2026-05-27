import { setRequestLocale } from "next-intl/server";

import { KitClient } from "./_client";

/**
 * Component kit / visual acceptance page for DESIGN.md §7 primitives.
 * Server-side wrapper that pins the locale before handing off to the
 * client demo (which owns the lucide icon imports — those can't cross
 * the server→client serialization boundary).
 */
export default async function KitPage({
  params,
}: {
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  setRequestLocale(locale);
  return <KitClient locale={locale} />;
}
