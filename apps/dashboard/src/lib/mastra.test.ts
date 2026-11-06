import { describe, expect, it, vi } from "vitest";

// mastra.ts 顶层 import 链含 `server-only` 与 `./backend`(后者再拉 next/headers),
// 在 node 测试环境会炸;mock 掉,只留待测的 ownsThread(纯逻辑 + 注入式 client)。
vi.mock("server-only", () => ({}));
vi.mock("./backend", () => ({
  BACKENDS: { mastra: "http://mastra.test" },
  getServiceToken: async () => "test-token",
  getSessionSubject: async () => "user:me",
}));

import type { MastraClient } from "@mastra/client-js";

const memory = vi.hoisted(() => ({
  get: vi.fn(),
  listMessages: vi.fn(),
}));

vi.mock("@mastra/client-js", () => ({
  MastraClient: class {
    getMemoryThread() { return memory; }
  },
}));

import { createRequire } from "node:module";
import { listChatMessages, ownsThread } from "./mastra";

/** Use the same AG-UI schema installed with the production Mastra adapter. */
const adapterRequire = createRequire(createRequire(import.meta.url).resolve("@ag-ui/mastra"));
const { RunAgentInputSchema } = adapterRequire("@ag-ui/core");

/** 造一个只实现 getMemoryThread().get() 的假 client。 */
function fakeClient(get: () => Promise<unknown>): MastraClient {
  return {
    getMemoryThread: () => ({ get }),
  } as unknown as MastraClient;
}

describe("ownsThread —— chat 会话越权(IDOR)防护", () => {
  it("thread.resourceId === 登录用户 → true(本人可读/改)", async () => {
    const client = fakeClient(async () => ({ resourceId: "user:me" }));
    expect(await ownsThread(client, "t1", "user:me")).toBe(true);
  });

  it("thread 属于他人 → false(不可跨租户读/改)", async () => {
    const client = fakeClient(async () => ({ resourceId: "user:other" }));
    expect(await ownsThread(client, "t1", "user:me")).toBe(false);
  });

  it("thread 无 resourceId → false", async () => {
    const client = fakeClient(async () => ({}));
    expect(await ownsThread(client, "t1", "user:me")).toBe(false);
  });

  it("get() 抛错(不存在/超时)→ false(当作不存在,失败关闭)", async () => {
    const client = fakeClient(async () => {
      throw new Error("not found");
    });
    expect(await ownsThread(client, "t1", "user:me")).toBe(false);
  });
});


describe("history continuation protocol", () => {
  it.each([
    { type: "tool-invocation", toolInvocation: {
      toolCallId: "call-1", toolName: "data.get_bars", args: { symbol: "BTC" },
      state: "result", result: { count: 24 },
    } },
    { type: "dynamic-tool", toolCallId: "call-1", toolName: "data.get_bars",
      input: { symbol: "BTC" }, output: { count: 24 }, state: "output-available" },
  ])("reloaded tool history can be submitted as an AG-UI run", async (part) => {
    memory.get.mockResolvedValue({ resourceId: "user:me" });
    memory.listMessages.mockResolvedValue({ messages: [
      { id: "m1", role: "user", content: "Fetch bars" },
      { id: "m2", role: "assistant", content: { parts: [part,
        { type: "text", text: "Fetched bars." }] } },
    ] });
    const messages = await listChatMessages("owned-thread");
    const continuation = {
      threadId: "owned-thread", runId: "next-run", state: {}, tools: [], context: [],
      forwardedProps: {}, messages: [...messages,
        { id: "m3", role: "user", content: "Continue" }],
    };
    expect(RunAgentInputSchema.safeParse(continuation).success).toBe(true);
    expect(messages.find((m) => m.role === "tool")?.toolCallId).toBe("call-1");
    expect(messages.find((m) => m.toolCalls)?.toolCalls?.[0].function.arguments)
      .toContain("BTC");
  });
});
