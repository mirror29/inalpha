"use client";
import { useTranslations } from "next-intl";
import useSWR from "swr";
import { Link } from "@/i18n/navigation";

/** Request receipts are an independent view, never added to candidate projections. */
export function UsageReceiptSummary({ operationId }: { operationId: string }) {
  const t = useTranslations("usage");
  const { data } = useSWR(`/api/usage?operation=${encodeURIComponent(operationId)}`, async (url: string) => {
    const response = await fetch(url);
    if (!response.ok) throw new Error("usage unavailable");
    return response.json() as Promise<{ summary: { calls: number; known_cost_usd: string | null; unknown_usage_calls: number; unknown_cost_calls: number; pending_calls: number } }>;
  });
  if (!data?.summary?.calls) return null;
  const summary = data.summary;
  return <section className="rounded-lg border border-border-subtle p-3 text-sm">
    <Link className="underline" href={`/usage?operation=${encodeURIComponent(operationId)}`}>{t("title")}</Link>
    <p>{t("cost")}: {summary.known_cost_usd === null ? t("unknown") : `$${Number(summary.known_cost_usd).toFixed(8)}`}</p>
    <p className="text-xs text-fg-muted">{t("incomplete", { usage: summary.unknown_usage_calls, cost: summary.unknown_cost_calls, pending: summary.pending_calls })}</p>
  </section>;
}
