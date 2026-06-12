/**
 * redact + insertPending 脱敏测试。
 *
 * 1. maskSensitive 纯函数：按字段名递归 mask 凭据 / PII，普通字段原样。
 * 2. permissions/repo.insertPending：入库前复用同一 mask，与 audit-log 对称
 *    （凭据/PII 不以明文落 pending_approvals.tool_input）。
 */
import { afterEach, describe, expect, it } from "vitest";

import type { Pool } from "pg";

import { insertPending, setPool } from "../src/permissions/repo.js";
import { maskSensitive } from "../src/redact.js";

describe("maskSensitive", () => {
  it("redacts credential + PII field names, keeps others", () => {
    const out = maskSensitive({
      url: "https://x.test",
      apiKey: "k-123",
      nested: { token: "t-abc", note: "ok" },
      list: [{ password: "p" }, { plain: 1 }],
    }) as Record<string, unknown>;
    expect(out.url).toBe("https://x.test");
    expect(out.apiKey).toBe("[REDACTED]");
    expect((out.nested as Record<string, unknown>).token).toBe("[REDACTED]");
    expect((out.nested as Record<string, unknown>).note).toBe("ok");
    expect((out.list as Record<string, unknown>[])[0].password).toBe("[REDACTED]");
    expect((out.list as Record<string, unknown>[])[1].plain).toBe(1);
  });

  it("normalizes case / 下划线 / 短横线（api_key / API-KEY 同命中）", () => {
    const out = maskSensitive({ api_key: "x", "ACCESS-KEY": "y" }) as Record<string, unknown>;
    expect(out.api_key).toBe("[REDACTED]");
    expect(out["ACCESS-KEY"]).toBe("[REDACTED]");
  });

  it("primitives / null 原样返回", () => {
    expect(maskSensitive(null)).toBe(null);
    expect(maskSensitive("plain")).toBe("plain");
    expect(maskSensitive(42)).toBe(42);
  });
});

describe("insertPending 脱敏入库", () => {
  afterEach(() => setPool(undefined));

  it("tool_input 入库前 mask 敏感字段", async () => {
    const params: unknown[][] = [];
    const mockPool = {
      query: async (_sql: string, p: unknown[]) => {
        params.push(p);
        return { rows: [] };
      },
    } as unknown as Pool;
    setPool(mockPool);

    await insertPending({
      requestId: "req-1",
      toolName: "web.fetch",
      toolInput: { url: "https://api.test", apiKey: "secret", nested: { token: "abc" } },
      createdAt: "2026-06-12T00:00:00Z",
      deadline: "2026-06-12T00:00:30Z",
    });

    const toolInputJson = params[0]?.[2] as string;
    const parsed = JSON.parse(toolInputJson) as Record<string, unknown>;
    expect(parsed.url).toBe("https://api.test"); // 普通字段保留
    expect(parsed.apiKey).toBe("[REDACTED]");
    expect((parsed.nested as Record<string, unknown>).token).toBe("[REDACTED]");
  });
});
