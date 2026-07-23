import { Hono } from "hono";
import { afterEach, describe, expect, it, vi } from "vitest";

import { identityMiddleware } from "../src/mastra/identity.js";

const TEST_KEY = "unique-test-key-must-never-reach-logs";

afterEach(() => {
  vi.restoreAllMocks();
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
});
