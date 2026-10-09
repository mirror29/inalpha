/** Non-secret, per-call chat usage; missing receipts never imply free calls. */
import type { EvolutionPricingSnapshot } from "./evolution-snapshot.js";

export type ChatUsageReceipt = {
  usageStatus: "known" | "unknown";
  inputTokens: number | null;
  outputTokens: number | null;
  cachedInputTokens: number | null;
  estimatedCostUsd: number | null;
  pricingVersion: string | null;
};

/** Accepts explicit zero, while rejecting missing, fractional or invalid counts. */
function tokenCount(value: unknown): number | null {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0
    ? value
    : null;
}

/**
 * Normalizes one Mastra step receipt, never its cumulative result as another call.
 * Cached tokens are retained as evidence; frozen non-cache rates give an estimate,
 * not a provider invoice. Unsupported pricing and incomplete usage remain unknown.
 */
export function normalizeChatUsage(
  usage: unknown,
  pricing?: EvolutionPricingSnapshot,
): ChatUsageReceipt {
  const source = usage && typeof usage === "object"
    ? usage as Record<string, unknown>
    : {};
  const inputTokens = tokenCount(source.inputTokens);
  const outputTokens = tokenCount(source.outputTokens);
  const cached = tokenCount(source.cachedInputTokens);
  const cachedInputTokens = cached !== null && inputTokens !== null && cached <= inputTokens
    ? cached
    : null;
  const usageStatus = inputTokens !== null && outputTokens !== null ? "known" : "unknown";
  const validPricing = pricing?.currency === "USD"
    && typeof pricing.version === "string" && pricing.version.trim() !== ""
    && Number.isFinite(pricing.input_usd_per_million) && pricing.input_usd_per_million >= 0
    && Number.isFinite(pricing.output_usd_per_million) && pricing.output_usd_per_million >= 0;
  let estimatedCostUsd: number | null = null;
  if (usageStatus === "known" && validPricing) {
    const estimate = (inputTokens! * pricing!.input_usd_per_million
      + outputTokens! * pricing!.output_usd_per_million) / 1_000_000;
    if (Number.isFinite(estimate)) estimatedCostUsd = Number(estimate.toFixed(12));
  }
  return {
    usageStatus,
    inputTokens,
    outputTokens,
    cachedInputTokens,
    estimatedCostUsd,
    pricingVersion: validPricing ? pricing!.version : null,
  };
}
