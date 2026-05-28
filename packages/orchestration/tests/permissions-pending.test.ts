/**
 * ``PendingApprovalsStore`` + permissions HTTP API 单测（D-9.1b / ADR-0018）。
 *
 * 覆盖：
 *
 * - request → respond=allow → promise 解为 allow + 池清空
 * - request → respond=deny → promise 解为 deny
 * - request → 超时 → 自动 deny + via=timeout
 * - list 返回不含 resolver 闭包的视图
 * - respond 对未知 id 返 false
 * - clearAll 全 deny + size=0
 * - API GET /permissions/pending 返列表
 * - API POST /permissions/:id/respond bad body / unknown id / 成功路径
 */
import { describe, expect, it, vi } from "vitest";

import { permissionsApiRoutes } from "../src/permissions/api.js";
import {
  PendingApprovalsStore,
  pendingApprovals as defaultPendingApprovals,
} from "../src/permissions/pending.js";

// ────────────────────────────────────────────────────────────────────
// PendingApprovalsStore
// ────────────────────────────────────────────────────────────────────

describe("PendingApprovalsStore.request → respond", () => {
  it("allow → promise resolves with decision='allow' + size=0", async () => {
    const store = new PendingApprovalsStore();
    const promise = store.request({
      toolName: "test.tool",
      toolInput: { x: 1 },
      timeoutMs: 5_000,
    });
    await vi.waitFor(() => expect(store.size()).toBe(1));
    const view = store.list()[0]!;
    expect(view.toolName).toBe("test.tool");
    expect(view.toolInput).toEqual({ x: 1 });
    expect(store.respond(view.requestId, "allow")).toBe(true);
    const result = await promise;
    expect(result.decision).toBe("allow");
    expect(result.via).toBe("user");
    expect(result.requestId).toBe(view.requestId);
    expect(store.size()).toBe(0);
  });

  it("deny → promise resolves with decision='deny'", async () => {
    const store = new PendingApprovalsStore();
    const promise = store.request({
      toolName: "t",
      toolInput: null,
      timeoutMs: 5_000,
    });
    await vi.waitFor(() => expect(store.size()).toBe(1));
    store.respond(store.list()[0]!.requestId, "deny");
    const result = await promise;
    expect(result.decision).toBe("deny");
    expect(result.via).toBe("user");
  });
});

describe("PendingApprovalsStore.request timeout", () => {
  it("auto-denies after timeoutMs + via='timeout'", async () => {
    const store = new PendingApprovalsStore();
    const result = await store.request({
      toolName: "t",
      toolInput: null,
      timeoutMs: 30,
    });
    expect(result.decision).toBe("deny");
    expect(result.via).toBe("timeout");
    expect(store.size()).toBe(0);
  });
});

describe("PendingApprovalsStore misc", () => {
  it("list view does not leak resolver/timer", () => {
    const store = new PendingApprovalsStore();
    store.request({ toolName: "t", toolInput: null, timeoutMs: 10_000 });
    const view = store.list()[0]!;
    expect(view).not.toHaveProperty("resolve");
    expect(view).not.toHaveProperty("timer");
    expect(view).toHaveProperty("requestId");
    expect(view).toHaveProperty("toolName");
    expect(view).toHaveProperty("createdAt");
    expect(view).toHaveProperty("deadline");
    store.clearAll();
  });

  it("respond returns false for unknown requestId", () => {
    const store = new PendingApprovalsStore();
    expect(store.respond("does-not-exist", "allow")).toBe(false);
  });

  it("clearAll resolves all as deny + size=0", async () => {
    const store = new PendingApprovalsStore();
    const p1 = store.request({ toolName: "a", toolInput: null, timeoutMs: 5_000 });
    const p2 = store.request({ toolName: "b", toolInput: null, timeoutMs: 5_000 });
    await vi.waitFor(() => expect(store.size()).toBe(2));
    store.clearAll();
    expect(store.size()).toBe(0);
    expect((await p1).decision).toBe("deny");
    expect((await p2).decision).toBe("deny");
  });

  it("module-level singleton exists and is a PendingApprovalsStore", () => {
    expect(defaultPendingApprovals).toBeInstanceOf(PendingApprovalsStore);
  });
});

// ────────────────────────────────────────────────────────────────────
// HTTP API（用 Hono 风格的 mock context；不拉真 server，直接调 handler）
// ────────────────────────────────────────────────────────────────────


function fakeContext(opts: {
  param?: Record<string, string>;
  body?: unknown;
  jsonThrows?: boolean;
}) {
  type JsonBody = {
    status: number;
    body: unknown;
  };
  const captured = { status: 200, body: null as unknown };
  return {
    captured,
    ctx: {
      req: {
        param: (key: string) => opts.param?.[key],
        json: async () => {
          if (opts.jsonThrows) throw new Error("invalid JSON");
          return opts.body;
        },
      },
      json: (body: unknown, status = 200): JsonBody => {
        captured.status = status;
        captured.body = body;
        return { status, body };
      },
    },
  };
}

describe("permissionsApiRoutes", () => {
  const listRoute = permissionsApiRoutes.find(
    (r) => r.path === "/permissions/pending" && r.method === "GET",
  )!;
  const respondRoute = permissionsApiRoutes.find(
    (r) => r.path === "/permissions/:id/respond" && r.method === "POST",
  )!;

  it("GET /permissions/pending returns list (default singleton)", async () => {
    defaultPendingApprovals.clearAll();
    // 注：handler 自动用 module-level singleton（不接受注入），所以挂一个真请求
    void defaultPendingApprovals.request({
      toolName: "t.x",
      toolInput: { a: 1 },
      timeoutMs: 30_000,
    });
    await vi.waitFor(() =>
      expect(defaultPendingApprovals.size()).toBeGreaterThan(0),
    );

    const { ctx, captured } = fakeContext({});
    // biome-ignore lint: hono Context shape simplified for unit test
    await listRoute.handler(ctx as any, async () => {});
    expect(captured.status).toBe(200);
    const body = captured.body as { pending: unknown[] };
    expect(body.pending.length).toBeGreaterThan(0);
    defaultPendingApprovals.clearAll();
  });

  it("POST /:id/respond rejects bad decision", async () => {
    const { ctx, captured } = fakeContext({
      param: { id: "anything" },
      body: { decision: "maybe" },
    });
    // biome-ignore lint: hono Context shape simplified for unit test
    await respondRoute.handler(ctx as any, async () => {});
    expect(captured.status).toBe(400);
  });

  it("POST /:id/respond returns 404 for unknown id", async () => {
    defaultPendingApprovals.clearAll();
    const { ctx, captured } = fakeContext({
      param: { id: "no-such-request" },
      body: { decision: "allow" },
    });
    // biome-ignore lint: hono Context shape simplified for unit test
    await respondRoute.handler(ctx as any, async () => {});
    expect(captured.status).toBe(404);
  });

  it("POST /:id/respond consumes the pending and resolves promise", async () => {
    defaultPendingApprovals.clearAll();
    const promise = defaultPendingApprovals.request({
      toolName: "t",
      toolInput: null,
      timeoutMs: 5_000,
    });
    await vi.waitFor(() => expect(defaultPendingApprovals.size()).toBe(1));
    const id = defaultPendingApprovals.list()[0]!.requestId;

    const { ctx, captured } = fakeContext({
      param: { id },
      body: { decision: "allow" },
    });
    // biome-ignore lint: hono Context shape simplified for unit test
    await respondRoute.handler(ctx as any, async () => {});
    expect(captured.status).toBe(200);
    expect((await promise).decision).toBe("allow");
    expect(defaultPendingApprovals.size()).toBe(0);
  });

  it("POST /:id/respond rejects non-JSON body", async () => {
    const { ctx, captured } = fakeContext({
      param: { id: "x" },
      jsonThrows: true,
    });
    // biome-ignore lint: hono Context shape simplified for unit test
    await respondRoute.handler(ctx as any, async () => {});
    expect(captured.status).toBe(400);
  });
});
