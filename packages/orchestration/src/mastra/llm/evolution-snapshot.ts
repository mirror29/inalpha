/** Immutable, non-secret LLM metadata bound to one evolution approval and run. */
import { createHash } from "node:crypto";

import type { UserLLMConfig } from "./provider.js";
import { DEFAULT_MODELS, PROVIDER_BASE_URLS } from "./provider.js";

export const USER_LLM_SNAPSHOT_KEY = "inalpha__evolutionLLMSnapshot";
export const APPROVAL_OPERATION_ID_KEY = "inalpha__approvalOperationId";

const PRICING_VERSION = "provider-estimate-2026-09";
const ASSUMED_INPUT_TOKENS = 24_000;
const MAX_OUTPUT_TOKENS = 8_192;
export const EVOLUTION_LLM_PROVIDERS = ["deepseek", "openai", "kimi", "zhipu"] as const;
export type EvolutionLLMProvider = (typeof EVOLUTION_LLM_PROVIDERS)[number];

const PRICED_MODELS: Record<
  EvolutionLLMProvider,
  Readonly<{ model: string; rates: readonly [number, number] }>
> = {
  deepseek: { model: "deepseek-flash", rates: [0.3, 1.2] },
  openai: { model: "gpt-5.5", rates: [5, 15] },
  kimi: { model: "kimi-k2.6", rates: [0.6, 2.5] },
  zhipu: { model: "glm-5.2", rates: [0.7, 2.8] },
};

export type EvolutionPricingSnapshot = {
  version: string;
  currency: "USD";
  input_usd_per_million: number;
  output_usd_per_million: number;
  assumed_input_tokens: number;
  max_output_tokens: number;
  estimated_max_usd_per_candidate: number;
};

export type EvolutionLLMSnapshot = {
  config_id: string;
  provider: EvolutionLLMProvider;
  model: string;
  base_url: string | null;
  pricing: EvolutionPricingSnapshot;
  config_digest: string;
};

/** Builds a frozen metadata snapshot without copying the user's API key. */
export function buildEvolutionLLMSnapshot(config: UserLLMConfig): EvolutionLLMSnapshot {
  if (!isEvolutionLLMProvider(config.provider)) {
    throw new Error(`evolution pricing is unavailable for provider ${config.provider}`);
  }
  const provider = config.provider;
  const priced = PRICED_MODELS[provider];
  const model =
    config.model?.trim() || DEFAULT_MODELS[provider as keyof typeof DEFAULT_MODELS];
  if (!model) throw new Error(`evolution model is unavailable for provider ${provider}`);
  if (model !== priced.model) {
    throw new Error(`evolution pricing is unavailable for model ${provider}/${model}`);
  }
  const baseUrl = normalizeProviderBaseUrl(
    provider,
    config.custom_base_url || PROVIDER_BASE_URLS[provider] || null,
  );
  const officialBaseUrl = normalizeProviderBaseUrl(
    provider,
    PROVIDER_BASE_URLS[provider] || null,
  );
  if (baseUrl !== officialBaseUrl) {
    throw new Error(`evolution requires the official ${provider} API endpoint`);
  }
  const pricing: EvolutionPricingSnapshot = {
    version: PRICING_VERSION,
    currency: "USD",
    input_usd_per_million: priced.rates[0],
    output_usd_per_million: priced.rates[1],
    assumed_input_tokens: ASSUMED_INPUT_TOKENS,
    max_output_tokens: MAX_OUTPUT_TOKENS,
    estimated_max_usd_per_candidate: Number(
      (
        (ASSUMED_INPUT_TOKENS * priced.rates[0] +
          MAX_OUTPUT_TOKENS * priced.rates[1]) /
        1_000_000
      ).toFixed(12),
    ),
  };
  const canonical = {
    config_id: config.id,
    provider,
    model,
    base_url: baseUrl,
    pricing,
  };
  return {
    ...canonical,
    config_digest: computeEvolutionLLMConfigDigest(canonical),
  };
}

/** Computes the cross-language digest used by Mastra and Evolver. */
export function computeEvolutionLLMConfigDigest(
  snapshot: Omit<EvolutionLLMSnapshot, "config_digest">,
): string {
  const pricing = snapshot.pricing;
  const canonical = [
    snapshot.config_id,
    snapshot.provider,
    snapshot.model,
    snapshot.base_url,
    pricing.version,
    pricing.currency,
    String(pricing.input_usd_per_million),
    String(pricing.output_usd_per_million),
    String(pricing.assumed_input_tokens),
    String(pricing.max_output_tokens),
    String(pricing.estimated_max_usd_per_candidate),
  ];
  return createHash("sha256").update(JSON.stringify(canonical)).digest("hex");
}

export function getRequestContextValue<T>(ctx: unknown, key: string): T | undefined {
  if (!ctx || typeof ctx !== "object") return undefined;
  const requestContext = (ctx as Record<string, unknown>).requestContext;
  if (!requestContext || typeof requestContext !== "object") return undefined;
  const getter = (requestContext as { get?: (name: string) => unknown }).get;
  const value =
    typeof getter === "function"
      ? getter.call(requestContext, key)
      : (requestContext as Record<string, unknown>)[key];
  return value as T | undefined;
}

export function setRequestContextValue(ctx: unknown, key: string, value: unknown): boolean {
  if (!ctx || typeof ctx !== "object") return false;
  const requestContext = (ctx as Record<string, unknown>).requestContext;
  if (!requestContext || typeof requestContext !== "object") return false;
  const setter = (requestContext as { set?: (name: string, next: unknown) => void }).set;
  if (typeof setter === "function") {
    setter.call(requestContext, key, value);
    return true;
  }
  (requestContext as Record<string, unknown>)[key] = value;
  return true;
}

function sanitizeBaseUrl(value: string | null): string | null {
  if (!value) return null;
  const url = new URL(value);
  if (!["http:", "https:"].includes(url.protocol) || !url.hostname) {
    throw new Error("LLM base URL must be an absolute HTTP URL");
  }
  if (url.username || url.password || url.search || url.hash) {
    throw new Error("LLM base URL cannot contain credentials, query, or fragment");
  }
  return url.toString().replace(/\/$/, "");
}

/** Canonicalizes documented aliases without accepting arbitrary proxy paths. */
function normalizeProviderBaseUrl(
  provider: EvolutionLLMProvider,
  value: string | null,
): string | null {
  const sanitized = sanitizeBaseUrl(value);
  if (provider === "deepseek" && sanitized === "https://api.deepseek.com/v1") {
    return "https://api.deepseek.com";
  }
  return sanitized;
}

function isEvolutionLLMProvider(value: string): value is EvolutionLLMProvider {
  return EVOLUTION_LLM_PROVIDERS.includes(value as EvolutionLLMProvider);
}
