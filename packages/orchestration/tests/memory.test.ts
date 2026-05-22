/**
 * 多用户隔离守卫单测（D-8b' review B11 / 高风险 #C 修后）。
 *
 * 验证 ``assertScopedRequest`` 在 caller 漏传 resourceId / threadId 时抛错，
 * 防止 prod 路径出现"不同用户共享同一空桶 → 历史互窜"的悄无声息泄漏。
 */
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
