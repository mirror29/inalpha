import { describe, expect, it } from "vitest";
import { normalizeChatUsage } from "../src/mastra/llm/chat-usage.js";
import { buildEvolutionLLMSnapshot } from "../src/mastra/llm/evolution-snapshot.js";

const pricing = buildEvolutionLLMSnapshot({
  id: "test-config", provider: "deepseek", model: "deepseek-flash", api_key: "test-only",
}).pricing;

describe("per-call chat usage receipts", () => {
  it("preserves a missing or failed response as unknown", () => {
    for (const usage of [undefined, null, {}, "error"]) {
      expect(normalizeChatUsage(usage, pricing)).toMatchObject({
        usageStatus: "unknown", inputTokens: null, outputTokens: null, estimatedCostUsd: null,
      });
    }
  });

  it("distinguishes explicit zero from missing counts", () => {
    expect(normalizeChatUsage({ inputTokens: 0, outputTokens: 0 }, pricing)).toMatchObject({
      usageStatus: "known", estimatedCostUsd: 0,
    });
    expect(normalizeChatUsage({ inputTokens: 0 }, pricing)).toMatchObject({
      usageStatus: "unknown", inputTokens: 0, outputTokens: null, estimatedCostUsd: null,
    });
  });

  it("rejects coerced, negative, fractional and nonfinite counts", () => {
    for (const inputTokens of ["5", -1, 0.5, NaN, Infinity, Number.MAX_SAFE_INTEGER + 1]) {
      expect(normalizeChatUsage({ inputTokens, outputTokens: 2 }, pricing)).toMatchObject({
        usageStatus: "unknown", inputTokens: null, outputTokens: 2, estimatedCostUsd: null,
      });
    }
  });

  it("retains small estimates and cache evidence without guessing a discount", () => {
    expect(normalizeChatUsage({ inputTokens: 1, outputTokens: 1, cachedInputTokens: 1 }, pricing))
      .toMatchObject({ usageStatus: "known", cachedInputTokens: 1, estimatedCostUsd: 0.0000015 });
    expect(normalizeChatUsage({ inputTokens: 1, outputTokens: 1, cachedInputTokens: 2 }, pricing))
      .toMatchObject({ cachedInputTokens: null, estimatedCostUsd: 0.0000015 });
  });

  it("keeps known tokens separate from an unknown monetary cost", () => {
    expect(normalizeChatUsage({ inputTokens: 20, outputTokens: 30 })).toMatchObject({
      usageStatus: "known", estimatedCostUsd: null, pricingVersion: null,
    });
    for (const rate of [-1, NaN, Infinity]) {
      expect(normalizeChatUsage({ inputTokens: 20, outputTokens: 30 }, {
        ...pricing, input_usd_per_million: rate,
      }).estimatedCostUsd).toBeNull();
    }
  });

  it("does not infer counts from aggregate totals or persist arbitrary response fields", () => {
    const receipt = normalizeChatUsage({ totalTokens: 50, api_key: "SECRET", text: "private" }, pricing);
    expect(receipt.usageStatus).toBe("unknown");
    expect(JSON.stringify(receipt)).not.toMatch(/SECRET|private|api_key|totalTokens/);
    expect(Object.keys(receipt)).toHaveLength(7);
  });
});

it("retains reasoning as an output subset", () => {
  const receipt = normalizeChatUsage({ inputTokens: 10, outputTokens: 20, reasoningTokens: 15 }, pricing);
  expect(receipt.reasoningTokens).toBe(15);
  expect(receipt.outputTokens).toBe(20);
});
