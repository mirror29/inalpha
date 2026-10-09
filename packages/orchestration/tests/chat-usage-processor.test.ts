import { Agent } from "@mastra/core/agent";
import { RequestContext } from "@mastra/core/request-context";
import { createTool } from "@mastra/core/tools";
import { z } from "zod";
import { describe, expect, it, vi } from "vitest";
import { mintServiceToken } from "../src/auth.js";
import { ScriptedModel } from "../src/evals/scripted-model.js";
import { AUTH_SUB_KEY } from "../src/hooks/with-hooks.js";
import { createChatUsageProcessor, CHAT_INVOCATION_ID_KEY } from "../src/mastra/llm/chat-usage-processor.js";
import { userLLMStore } from "../src/mastra/llm/provider.js";
import { buildEvolutionLLMSnapshot, USER_LLM_SNAPSHOT_KEY } from "../src/mastra/llm/evolution-snapshot.js";
import type { ChatCallIdentity, ChatUsageStore } from "../src/mastra/llm/chat-usage-store.js";
import type { ChatUsageReceipt } from "../src/mastra/llm/chat-usage.js";

/** Drives the real pinned Agent loop with an isolated in-memory receipt sink. */
function fixture(turns: ConstructorParameters<typeof ScriptedModel>[1]) {
  const calls: ChatCallIdentity[] = [];
  const receipts: ChatUsageReceipt[] = [];
  const events: string[] = [];
  const store: ChatUsageStore = {
    begin: vi.fn(async (call) => { calls.push(call); events.push("begin"); }),
    settle: vi.fn(async (_, receipt) => { receipts.push(receipt); events.push("settle"); }),
  };
  const processor = createChatUsageProcessor(store);
  const model = new ScriptedModel("chat-usage-test", turns);
  const requestContext = new RequestContext();
  requestContext.set(AUTH_SUB_KEY, "owner-test");
  requestContext.set(CHAT_INVOCATION_ID_KEY, "untrusted-client-value");
  requestContext.set(USER_LLM_SNAPSHOT_KEY, buildEvolutionLLMSnapshot({
    id: "model-test", provider: "deepseek", model: "deepseek-flash", api_key: "SECRET",
  }));
  const agent = new Agent({ id: "usage-test", name: "usage-test", instructions: "test",
    model: model.asLanguageModel(), maxRetries: 0,
    inputProcessors: [processor], outputProcessors: [processor],
    tools: { read: createTool({ id: "read", description: "test", inputSchema: z.object({}),
      execute: async () => { events.push("tool"); return "done"; } }) },
  });
  const generate: typeof agent.generate = (...args) => userLLMStore.run({
    id: "model-test", provider: "deepseek", model: "deepseek-flash", api_key: "SECRET",
  }, () => agent.generate(...args));
  return { agent, generate, model, requestContext, calls, receipts, events, store };
}

describe("real Agent per-call usage processing", () => {
  it("shares invocation state across steps, settles before tools, and avoids aggregate double count", async () => {
    const f = fixture([{ type: "tool-call", call: { tool: "read", input: {} } }, { type: "text", text: "done" }]);
    await f.generate("test", { requestContext: f.requestContext });
    f.model.assertConsumed();
    expect(f.events).toEqual(["begin", "settle", "tool", "begin", "settle"]);
    expect(f.calls).toHaveLength(2);
    expect(f.receipts).toHaveLength(2);
    expect(f.calls[0].invocationId).toBe(f.calls[1].invocationId);
    expect(f.calls[0].callId).not.toBe(f.calls[1].callId);
    expect(f.calls.map((call) => call.stepNumber)).toEqual([0, 1]);
    expect(f.calls[0].invocationId).not.toBe("untrusted-client-value");
    expect(f.receipts[0]).toMatchObject({ usageStatus: "known", estimatedCostUsd: 0.0000015 });
    expect(JSON.stringify(f.calls)).not.toContain("SECRET");
  });

  it("uses a fresh invocation for a later user request", async () => {
    const f = fixture([{ type: "text", text: "one" }, { type: "text", text: "two" }]);
    await f.generate("one", { requestContext: f.requestContext });
    await f.generate("two", { requestContext: f.requestContext });
    expect(f.calls[0].invocationId).not.toBe(f.calls[1].invocationId);
  });

  it("prevents a model call if its initial receipt cannot persist", async () => {
    const f = fixture([{ type: "text", text: "unused" }]);
    vi.mocked(f.store.begin).mockRejectedValue(new Error("storage unavailable"));
    await expect(f.generate("test", { requestContext: f.requestContext })).rejects.toThrow();
    expect(f.model.calls).toHaveLength(0);
  });

  it("keeps an unknown call when the model fails and does not silently retry it", async () => {
    const f = fixture([]);
    await expect(f.generate("test", { requestContext: f.requestContext })).rejects.toThrow();
    expect(f.calls).toHaveLength(1);
    expect(f.model.calls).toHaveLength(1);
    expect(f.receipts).toEqual([expect.objectContaining({ usageStatus: "unknown", estimatedCostUsd: null })]);
  });

  it("records verified background calls separately from user chat", async () => {
    const f = fixture([{ type: "text", text: "done" }]);
    const requestContext = new RequestContext([["authToken", await mintServiceToken({ sub: "service:test" })]]);
    await f.agent.generate("test", { requestContext });
    expect(f.calls[0]).toMatchObject({ authSub: "service:test", source: "service" });
    expect(f.receipts[0]).toMatchObject({ usageStatus: "known", estimatedCostUsd: null });
  });

  it("does not price calls from a request-context snapshot without the actual model configuration", async () => {
    const f = fixture([{ type: "text", text: "done" }]);
    await f.agent.generate("test", { requestContext: f.requestContext });
    expect(f.calls[0].pricing).toBeUndefined();
    expect(f.receipts[0]).toMatchObject({ usageStatus: "known", estimatedCostUsd: null });
  });

  it("blocks an unverified background identity before model calls", async () => {
    const f = fixture([{ type: "text", text: "unused" }]);
    await expect(f.generate("test", { requestContext: new RequestContext([["authToken", "invalid"]]) })).rejects.toThrow();
    expect(f.calls).toHaveLength(0);
    expect(f.model.calls).toHaveLength(0);
  });

  it("preserves the completed reply if settlement fails", async () => {
    const f = fixture([{ type: "text", text: "done" }]);
    vi.mocked(f.store.settle).mockRejectedValue(new Error("storage unavailable"));
    const warning = vi.spyOn(console, "warn").mockImplementation(() => {});
    try {
      expect((await f.generate("test", { requestContext: f.requestContext })).text).toBe("done");
      expect(f.calls).toHaveLength(1);
      expect(f.receipts).toHaveLength(0);
    } finally { warning.mockRestore(); }
  });
});
