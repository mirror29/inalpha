import { setRequestLocale } from "next-intl/server";
import { UsageClient } from "@/components/usage/UsageClient";

/** Authenticated, owner-scoped LLM accounting. */
export default async function UsagePage({ params, searchParams }: { params: Promise<{ locale: string }>; searchParams: Promise<{ operation?: string }> }) {
  const { locale } = await params;
  setRequestLocale(locale);
  const { operation } = await searchParams;
  return <UsageClient initialOperation={typeof operation === "string" ? operation : ""} />;
}
