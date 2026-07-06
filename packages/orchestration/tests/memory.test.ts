/**
 * 多用户隔离守卫单测（D-8b' review B11 / 高风险 #C 修后）。
 *
 * 验证 ``assertScopedRequest`` 在 caller 漏传 resourceId / threadId 时抛错，
 * 防止 prod 路径出现"不同用户共享同一空桶 → 历史互窜"的悄无声息泄漏。
 */
import { LibSQLStore } from "@mastra/libsql";
import { Memory } from "@mastra/memory";
import { describe, expect, it } from "vitest";

import { MissingScopeError, assertScopedRequest, sharedMemory } from "../src/mastra/memory.js";

describe("assertScopedRequest", () => {
  it("passes when both ids present", () => {
    expect(() =>
      assertScopedRequest({ resourceId: "u-1", threadId: "t-1" }),
    ).not.toThrow();
  });

  it("throws MissingScopeError when resourceId absent", () => {
    expect(() => assertScopedRequest({ threadId: "t-1" })).toThrow(MissingScopeError);
  });

  it("throws MissingScopeError when threadId absent", () => {
    expect(() => assertScopedRequest({ resourceId: "u-1" })).toThrow(MissingScopeError);
  });

  it("throws when both empty strings", () => {
    expect(() => assertScopedRequest({ resourceId: "", threadId: "" })).toThrow(
      MissingScopeError,
    );
  });

  it("error message lists which ids were missing", () => {
    try {
      assertScopedRequest({});
    } catch (e) {
      expect(e).toBeInstanceOf(MissingScopeError);
      const msg = (e as Error).message;
      expect(msg).toContain("resourceId");
      expect(msg).toContain("threadId");
      expect(msg).toContain("leak across users");
    }
  });

  it("rejects non-string types", () => {
    expect(() =>
      assertScopedRequest({ resourceId: 123 as unknown as string, threadId: "t-1" }),
    ).toThrow(MissingScopeError);
  });
});

describe("sharedMemory", () => {
  it("is exported and constructed", () => {
    // 这层 export 是 orchestrator / trader / risk agent 共用的；只验存在
    // （实际 IO 测试需要 LibSQLStore 真起 SQLite，留给 integration test）
    expect(sharedMemory).toBeDefined();
  });
});

/**
 * chat 会话按 resourceId 隔离的**底层保证**(issue #130 回归守卫)。
 *
 * 聊天发送路径:dashboard 把可信 resourceId(= 登录用户 sub)传给 mastra agent.stream,
 * threadId 由客户端控制。若 mastra 不按 resourceId 校验 thread 归属,登录用户 B 拿到 A 的
 * threadId 就能读/写 A 的会话(IDOR)。本测试直证:LibSQLStore(生产所用 store)的
 * getThreadById 传入 resourceId 时按其过滤——**他人 resourceId 一律取不到**,即 mastra
 * 加载 B 的上下文时拿不到 A 的 thread。若未来升级 mastra 悄悄去掉该过滤,此测试会红。
 */
describe("thread 按 resourceId 隔离 (issue #130 回归)", () => {
  it("A 的 thread 用 B 的 resourceId getThreadById → null;本人 → 取得到", async () => {
    // 用 Memory 走 agent 实际加载 thread 的那条 API(mastra agent.stream → memory.getThreadById)。
    const mem = new Memory({ storage: new LibSQLStore({ id: "iso-test", url: ":memory:" }) });
    await mem.createThread({ threadId: "T-a", resourceId: "user:A" });

    const asOther = await mem.getThreadById({ threadId: "T-a", resourceId: "user:B" });
    expect(asOther).toBeNull(); // 他人拿不到 → chat 越权被底层拦住

    const asOwner = await mem.getThreadById({ threadId: "T-a", resourceId: "user:A" });
    expect(asOwner?.resourceId).toBe("user:A"); // 本人正常
  });
});
