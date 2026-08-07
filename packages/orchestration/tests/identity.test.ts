import { Hono } from "hono";
import { afterEach, describe, expect, it, vi } from "vitest";

import { mintServiceToken } from "../src/auth.js";
import { AUTH_SUB_KEY } from "../src/hooks/with-hooks.js";
import { identityMiddleware } from "../src/mastra/identity.js";

const TEST_KEY = "unique-test-key-must-never-reach-logs";

function appWithRequestContext(requestContext: Map<string, unknown>) {
  const app = new Hono();
  app.use("*", async (context, next) => {
    (context.set as (key: string, value: unknown) => void)("requestContext", requestContext);
    await next();
  });
  app.use("*", identityMiddleware);
  app.get("/", (context) => context.text("ok"));
  return app;
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllEnvs();
  vi.resetModules();
});

describe("identityMiddleware", () => {
  it("does not log the raw LLM configuration header or API key", async () => {
    const app = new Hono();
    const log = vi.spyOn(console, "log").mockImplementation(() => {});
    app.use("*", identityMiddleware);
    app.get("/", (context) => context.text("ok"));

    const response = await app.request("/", {
      headers: {
        "X-LLM-Config": JSON.stringify({
          id: "config-1",
          provider: "anthropic",
          model: "claude-test",
          api_key: TEST_KEY,
        }),
      },
    });

    expect(response.status).toBe(200);
    const output = log.mock.calls.flat().map(String).join(" ");
    expect(output).not.toContain(TEST_KEY);
    expect(output).not.toContain("X-LLM-Config");
    expect(log).toHaveBeenCalledWith("[identity-mw] Parsed userConfig:", {
      id: "config-1",
      provider: "anthropic",
      model: "claude-test",
    });
  });

  it("injects the verified Bearer subject into request context", async () => {
    const requestContext = new Map<string, unknown>();
    const response = await appWithRequestContext(requestContext).request("/", {
      headers: { Authorization: `Bearer ${await mintServiceToken({ sub: "user:alice" })}` },
    });

    expect(response.status).toBe(200);
    expect(requestContext.get(AUTH_SUB_KEY)).toBe("user:alice");
  });

  it("does not inject a subject for an invalid Bearer token", async () => {
    const requestContext = new Map<string, unknown>();
    const response = await appWithRequestContext(requestContext).request("/", {
      headers: { Authorization: "Bearer invalid-token" },
    });

    expect(response.status).toBe(200);
    expect(requestContext.has(AUTH_SUB_KEY)).toBe(false);
  });
});

describe("buildUserAwareModel", () => {
  it("requires a user LLM configuration when authentication is enabled", async () => {
    vi.stubEnv("AUTH_ENABLED", "true");
    vi.resetModules();
    const { buildUserAwareModel } = await import("../src/mastra/llm/provider.js");

    expect(() => buildUserAwareModel().doGenerate).toThrow("用户未配置 LLM API Key");
  });

  it("uses an ALS-scoped user configuration when authentication is enabled", async () => {
    vi.stubEnv("AUTH_ENABLED", "true");
    vi.resetModules();
    const { buildUserAwareModel, userLLMStore } = await import("../src/mastra/llm/provider.js");
    const model = buildUserAwareModel();

    expect(() => userLLMStore.run({
      id: "config-1",
      provider: "anthropic",
      api_key: "user-key",
    }, () => model.doGenerate)).not.toThrow();
  });

  // 回归：mastra 1.36 resolveModelConfig 用 `"specificationVersion" in modelConfig`
  // 判定模型是 AI SDK v5/v6。proxy 必须让 `in` 命中真实模型，否则抛
  // "Invalid model configuration provided"（即线上 "Failed to resolve model configuration"）。
  it("forwards property membership (in) checks to the real model", async () => {
    vi.stubEnv("AUTH_ENABLED", "true");
    vi.resetModules();
    const { buildUserAwareModel, userLLMStore } = await import("../src/mastra/llm/provider.js");
    const model = buildUserAwareModel();
    const userConfig = { id: "config-1", provider: "anthropic", api_key: "user-key" };

    const spec = await userLLMStore.run(userConfig, () => {
      expect("specificationVersion" in model).toBe(true);
      return model.specificationVersion;
    });

    // 必须是 mastra 1.36 认可的 v2(AI SDK v5)/v3(AI SDK v6)，否则 stream() 仍报不兼容
    expect(["v2", "v3"]).toContain(spec);
  });
});
