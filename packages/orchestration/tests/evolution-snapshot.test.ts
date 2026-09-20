import { describe, expect, it } from "vitest";

import {
  buildEvolutionLLMSnapshot,
  computeEvolutionLLMConfigDigest,
} from "../src/mastra/llm/evolution-snapshot.js";

describe("evolution LLM snapshot", () => {
  it("matches the Python cross-language digest contract without copying the API key", () => {
    const snapshot = buildEvolutionLLMSnapshot({
      id: "config-1",
      provider: "deepseek",
      api_key: "must-not-be-copied",
      custom_base_url: "https://api.deepseek.com/",
    });

    expect(snapshot).toMatchObject({
      model: "deepseek-flash",
      pricing: {
        version: "provider-estimate-2026-09",
        input_usd_per_million: 0.3,
        output_usd_per_million: 1.2,
      },
    });
    expect(JSON.stringify(snapshot)).not.toContain("must-not-be-copied");
    expect(computeEvolutionLLMConfigDigest(snapshot)).toBe(snapshot.config_digest);
  });

  it.each([
    ["openai", "gpt-5.5", 5, 15],
    ["kimi", "kimi-k2.6", 0.6, 2.5],
    ["zhipu", "glm-5.2", 0.7, 2.8],
  ] as const)(
    "freezes the default model, pricing, and digest for %s",
    (provider, model, inputRate, outputRate) => {
      const snapshot = buildEvolutionLLMSnapshot({
        id: `config-${provider}`,
        provider,
        api_key: "must-not-be-copied",
      });

      expect(snapshot).toMatchObject({
        provider,
        model,
        pricing: {
          input_usd_per_million: inputRate,
          output_usd_per_million: outputRate,
        },
      });
      expect(snapshot.config_digest).toMatch(/^[a-f0-9]{64}$/);
      expect(computeEvolutionLLMConfigDigest(snapshot)).toBe(snapshot.config_digest);
      expect(JSON.stringify(snapshot)).not.toContain("must-not-be-copied");
    },
  );

  it("fails closed for providers the Python runtime cannot execute", () => {
    expect(() =>
      buildEvolutionLLMSnapshot({
        id: "config-2",
        provider: "anthropic",
        api_key: "test-key",
      }),
    ).toThrow("pricing is unavailable");
  });

  it("rejects credentials, query strings, and non-HTTP base URLs", () => {
    const credentialUrl = new URL("https://example.com/v1");
    credentialUrl.username = "user";
    credentialUrl.password = "pass";
    for (const custom_base_url of [
      credentialUrl.toString(),
      "https://example.com/v1?token=x",
      "ftp://example.com/v1",
    ]) {
      expect(() =>
        buildEvolutionLLMSnapshot({
          id: "config-3",
          provider: "openai",
          api_key: "test-key",
          custom_base_url,
        }),
      ).toThrow();
    }
  });

  it("rejects custom proxy and private-network endpoints for evolution", () => {
    for (const custom_base_url of [
      "https://proxy.example.com/v1",
      "http://127.0.0.1:8080/v1",
      "http://169.254.169.254/latest/meta-data",
    ]) {
      expect(() =>
        buildEvolutionLLMSnapshot({
          id: "config-4",
          provider: "openai",
          api_key: "test-key",
          custom_base_url,
        }),
      ).toThrow("official openai API endpoint");
    }
  });

  it("canonicalizes DeepSeek's documented /v1 alias", () => {
    const snapshot = buildEvolutionLLMSnapshot({
      id: "config-deepseek-v1",
      provider: "deepseek",
      model: "deepseek-flash",
      api_key: "test-key",
      custom_base_url: "https://api.deepseek.com/v1",
    });

    expect(snapshot.base_url).toBe("https://api.deepseek.com");
  });

  it("rejects models without a frozen, model-specific pricing entry", () => {
    expect(() =>
      buildEvolutionLLMSnapshot({
        id: "config-unpriced",
        provider: "openai",
        model: "gpt-unknown-expensive",
        api_key: "test-key",
      }),
    ).toThrow("pricing is unavailable for model");
  });
});
