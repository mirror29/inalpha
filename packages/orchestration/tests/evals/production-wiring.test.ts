import { describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  model: {
    specificationVersion: "v2",
    provider: "production-test",
    modelId: "production-test",
    supportedUrls: {},
  },
  memory: {},
  processor: { id: "pending-plan-notice" },
  builtin: { id: "builtin.read", execute: async () => "builtin" },
  mcp: { id: "mcp__fixture__read", execute: async () => "mcp" },
  loadMcp: vi.fn(),
}));

vi.mock("../../src/env.js", () => ({}));
vi.mock("../../src/mastra/llm/provider.js", () => ({
  buildUserAwareModel: () => mocks.model,
}));
vi.mock("../../src/mastra/memory.js", () => ({ sharedMemory: mocks.memory }));
vi.mock("../../src/hooks/index.js", () => ({
  createPaperPendingPlanFetcher: () => vi.fn(),
  createPendingPlanNoticeProcessor: () => mocks.processor,
}));
vi.mock("../../src/mastra/wired-tools.js", () => ({
  wiredOrchestratorTools: [mocks.builtin],
  loadWiredMcpTools: async () => {
    mocks.loadMcp();
    return [mocks.mcp];
  },
}));

import { orchestrator } from "../../src/mastra/agents/orchestrator.js";

describe("production Orchestrator wiring", () => {
  it("preserves model, memory, processors, and dynamic MCP tools", async () => {
    expect(orchestrator).toMatchObject({ id: "orchestrator", name: "orchestrator" });
    expect(await orchestrator.getDefaultOptions()).toMatchObject({ maxSteps: 40 });
    expect(orchestrator.hasOwnMemory()).toBe(true);
    expect(
      (await orchestrator.listConfiguredOutputProcessors()).map(
        (processor) => processor.id,
      ),
    ).toEqual(["chat-usage-receipts", "pending-plan-notice"]);

    const tools = await orchestrator.listTools();
    expect(mocks.loadMcp).toHaveBeenCalledOnce();
    expect(Object.keys(tools)).toEqual(["builtin.read", "mcp__fixture__read"]);
  });
});
