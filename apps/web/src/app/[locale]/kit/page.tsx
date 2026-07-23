import type { Metadata } from "next";
import { setRequestLocale } from "next-intl/server";

import { KitClient } from "./_client";

export const metadata: Metadata = {
  title: "Inalpha Component Kit",
  description: "Internal visual acceptance page for Inalpha design primitives.",
  robots: {
    index: false,
    follow: false,
  },
};

export default async function KitPage({
  params,
}: {
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  setRequestLocale(locale);
  return <KitClient locale={locale} />;
}
