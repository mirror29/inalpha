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

import { ownsThread } from "./mastra";

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
