/**
 * Hooks 层单测：matcher / runner / with-hooks 三个模块的核心行为。
 *
 * 不依赖 Mastra runtime —— withHooks 接受任意"鸭子 tool"（id + execute），可
 * 直接构造测试 fixture。
 */
import { describe, expect, it, vi } from "vitest";

import {
  DEFAULT_GRID_MAX,
  HookRunner,
  createAuditLogHandler,
  createGridSizeCapHandler,
  createToolIdempotencyHandlers,
  defaultAuditRegistration,
  defaultGetSessionId,
  defaultGridSizeCapRegistration,
  defaultIdempotencyRegistrations,
  toolMatches,
  withHooks,
} from "../src/hooks/index.js";

// ────────────────────────────────────────────────────────────────────
// matcher
// ────────────────────────────────────────────────────────────────────

describe("toolMatches", () => {
  it("matches exact tool name", () => {
    expect(toolMatches("paper.run_backtest", "paper.run_backtest")).toBe(true);
    expect(toolMatches("paper.run_backtest", "paper.list_strategies")).toBe(false);
  });

  it("matches prefix wildcard ``.*``", () => {
    expect(toolMatches("paper.*", "paper.run_backtest")).toBe(true);
    expect(toolMatches("paper.*", "paper.list_strategies")).toBe(true);
    expect(toolMatches("paper.*", "data.get_bars")).toBe(false);
  });

  it("matches global ``*``", () => {
    expect(toolMatches("*", "anything")).toBe(true);
  });

  it("matches OR of multiple patterns", () => {
    const m = "paper.* | data.get_bars";
    expect(toolMatches(m, "paper.run_backtest")).toBe(true);
    expect(toolMatches(m, "data.get_bars")).toBe(true);
    expect(toolMatches(m, "data.backfill_bars")).toBe(false);
  });

  it("no matcher = always match", () => {
    expect(toolMatches(undefined, "anything")).toBe(true);
  });

  it("no toolName + non-empty matcher = no match", () => {
    expect(toolMatches("paper.*", undefined)).toBe(false);
  });
});

// ────────────────────────────────────────────────────────────────────
// runner
// ────────────────────────────────────────────────────────────────────

describe("HookRunner", () => {
  it("invokes only handlers matching the event", async () => {
    const runner = new HookRunner();
    const pre = vi.fn();
    const post = vi.fn();

    runner.register({ id: "p1", event: "PreToolUse", handler: pre });
    runner.register({ id: "p2", event: "PostToolUse", handler: post });

    await runner.run("PreToolUse", { toolName: "x" });
    expect(pre).toHaveBeenCalledTimes(1);
    expect(post).not.toHaveBeenCalled();
  });

  it("invokes only handlers matching the matcher", async () => {
    const runner = new HookRunner();
    const h1 = vi.fn();
    const h2 = vi.fn();

    runner.register({ id: "h1", event: "PreToolUse", matcher: "paper.*", handler: h1 });
    runner.register({ id: "h2", event: "PreToolUse", matcher: "data.*", handler: h2 });

    await runner.run("PreToolUse", { toolName: "paper.run_backtest" });
    expect(h1).toHaveBeenCalled();
    expect(h2).not.toHaveBeenCalled();
  });

  it("merges permissionOverride with deny > ask > allow", async () => {
    const runner = new HookRunner();
    runner.register({
      id: "allow",
      event: "PreToolUse",
      handler: () => ({ permissionOverride: "allow" }),
    });
    runner.register({
      id: "ask",
      event: "PreToolUse",
      handler: () => ({ permissionOverride: "ask" }),
    });

    const d = await runner.run("PreToolUse", { toolName: "x" });
    expect(d.permissionOverride).toBe("ask");
  });

  it("stops chain at first deny", async () => {
    const runner = new HookRunner();
    const later = vi.fn();

    runner.register({
      id: "early-deny",
      event: "PreToolUse",
      handler: () => ({ permissionOverride: "deny", message: "no" }),
    });
    runner.register({ id: "later", event: "PreToolUse", handler: later });

    const d = await runner.run("PreToolUse", { toolName: "x" });
    expect(d.permissionOverride).toBe("deny");
    expect(later).not.toHaveBeenCalled();
  });

  it("blocking hook failure → deny + message + chain stops", async () => {
    const runner = new HookRunner();
    const later = vi.fn();
    runner.register({
      id: "boom",
      event: "PreToolUse",
      handler: () => {
        throw new Error("oops");
      },
      blocking: true,
    });
    runner.register({ id: "later", event: "PreToolUse", handler: later });

    const d = await runner.run("PreToolUse", { toolName: "x" });
    expect(d.permissionOverride).toBe("deny");
    expect(d.message).toContain("hook boom failed: oops");
    expect(later).not.toHaveBeenCalled();
  });

  it("non-blocking hook failure → warn only, chain continues", async () => {
    const runner = new HookRunner();
    const later = vi.fn();
    runner.register({
      id: "warn",
      event: "PreToolUse",
      handler: () => {
        throw new Error("flaky");
      },
      blocking: false,
    });
    runner.register({ id: "later", event: "PreToolUse", handler: later });

    const d = await runner.run("PreToolUse", { toolName: "x" });
    expect(d.permissionOverride).toBeUndefined();
    expect(d.message).toContain("warn: flaky");
    expect(later).toHaveBeenCalled();
  });

  it("hook timeout → blocking deny", async () => {
    const runner = new HookRunner();
    runner.register({
      id: "slow",
      event: "PreToolUse",
      handler: () => new Promise(() => {}), // 永不 resolve
      timeoutMs: 30,
      blocking: true,
    });

    const d = await runner.run("PreToolUse", { toolName: "x" });
    expect(d.permissionOverride).toBe("deny");
    expect(d.message).toContain("timed out after 30ms");
  });

  it("hook resolved AFTER timeout does not throw unhandled rejection (review B12)", async () => {
    // 测延迟 reject 被 runner 静默吞掉：
    // 1) runner timeout → 返 deny
    // 2) 50ms 后 handler 才 reject —— 不应触发 unhandledRejection
    const runner = new HookRunner();
    runner.register({
      id: "late-reject",
      event: "PreToolUse",
      handler: () =>
        new Promise<void>((_, reject) => {
          setTimeout(() => reject(new Error("late!")), 50);
        }),
      timeoutMs: 10,
      blocking: true,
    });

    let unhandled: unknown = null;
    const onUnhandled = (e: Error | unknown): void => {
      unhandled = e;
    };
    process.on("unhandledRejection", onUnhandled);
    try {
      const d = await runner.run("PreToolUse", { toolName: "x" });
      expect(d.permissionOverride).toBe("deny");
      // 等延迟 reject 触发
      await new Promise((r) => setTimeout(r, 80));
      expect(unhandled).toBeNull();
    } finally {
      process.off("unhandledRejection", onUnhandled);
    }
  });

  it("updatedInput from later hook overrides earlier", async () => {
    const runner = new HookRunner();
    runner.register({
      id: "a",
      event: "PreToolUse",
      handler: () => ({ updatedInput: { a: 1 } }),
    });
    runner.register({
      id: "b",
      event: "PreToolUse",
      handler: () => ({ updatedInput: { a: 2 } }),
    });

    const d = await runner.run("PreToolUse", { toolName: "x", toolInput: {} });
    expect(d.updatedInput).toEqual({ a: 2 });
  });

  it("messages from multiple hooks concatenate with newline", async () => {
    const runner = new HookRunner();
    runner.register({ id: "a", event: "PostToolUse", handler: () => ({ message: "one" }) });
    runner.register({ id: "b", event: "PostToolUse", handler: () => ({ message: "two" }) });

    const d = await runner.run("PostToolUse", { toolName: "x" });
    expect(d.message).toBe("one\ntwo");
  });

  it("Stop hook continue=false carries reason", async () => {
    const runner = new HookRunner();
    runner.register({
      id: "force-continue",
      event: "Stop",
      handler: () => ({ continue: false, reason: "plan still pending" }),
    });

    const d = await runner.run("Stop", {});
    expect(d.continue).toBe(false);
    expect(d.reason).toBe("plan still pending");
  });
});

// ────────────────────────────────────────────────────────────────────
// withHooks（execute middleware）
// ────────────────────────────────────────────────────────────────────

function makeTool(impl: (input: unknown) => unknown | Promise<unknown>) {
  return {
    id: "test.tool",
    description: "fixture",
    execute: async (input: unknown) => impl(input),
  };
}

describe("withHooks", () => {
  it("calls original execute when no hooks present", async () => {
    const runner = new HookRunner();
    const tool = makeTool((input) => ({ echo: input }));
    const wrapped = withHooks(tool, { runner });

    const out = await wrapped.execute!({ x: 1 });
    expect(out).toEqual({ echo: { x: 1 } });
  });

  it("PreToolUse deny prevents execute and surfaces message", async () => {
    const runner = new HookRunner();
    const exec = vi.fn();
    const tool = { id: "test.tool", description: "", execute: exec };
    runner.register({
      id: "block",
      event: "PreToolUse",
      handler: () => ({ permissionOverride: "deny", message: "policy says no" }),
    });
    const wrapped = withHooks(tool, { runner });

    const out = (await wrapped.execute!({})) as { isError: boolean; message: string };
    expect(exec).not.toHaveBeenCalled();
    expect(out.isError).toBe(true);
    expect(out.message).toBe("policy says no");
  });

  it("PreToolUse updatedInput rewrites the input passed to execute", async () => {
    const runner = new HookRunner();
    let captured: unknown;
    const tool = makeTool((input) => {
      captured = input;
      return { ok: true };
    });
    runner.register({
      id: "rewrite",
      event: "PreToolUse",
      handler: () => ({ updatedInput: { clamped: true } }),
    });
    const wrapped = withHooks(tool, { runner });

    await wrapped.execute!({ raw: true });
    expect(captured).toEqual({ clamped: true });
  });

  it("permissionResolver=deny when hook is silent → blocks execute", async () => {
    const runner = new HookRunner();
    const exec = vi.fn();
    const tool = { id: "test.tool", description: "", execute: exec };
    const wrapped = withHooks(tool, {
      runner,
      permissionResolver: () => "deny",
    });

    const out = (await wrapped.execute!({})) as { isError: boolean; deniedBy: string };
    expect(exec).not.toHaveBeenCalled();
    expect(out.isError).toBe(true);
    expect(out.deniedBy).toBe("permission");
  });

  it("ask 第一次调 → 返 requiresApproval + 不调 execute (D-9.1b session cache)", async () => {
    const { PendingApprovalsStore } = await import("../src/permissions/pending.js");
    const { AskApprovalCache } = await import("../src/permissions/ask-cache.js");
    const store = new PendingApprovalsStore();
    const cache = new AskApprovalCache(5_000);
    const runner = new HookRunner();
    const exec = vi.fn();
    const tool = { id: "test.tool", description: "", execute: exec };
    const wrapped = withHooks(tool, {
      runner,
      permissionResolver: () => "ask",
      pendingApprovals: store,
      askCache: cache,
    });

    const out = (await wrapped.execute!({ x: 1 })) as {
      isError: boolean;
      deniedBy: string;
      requiresApproval: boolean;
      toolName: string;
      toolInput: unknown;
      message: string;
    };

    expect(exec).not.toHaveBeenCalled();
    expect(out.isError).toBe(true);
    expect(out.deniedBy).toBe("permission-ask");
    expect(out.requiresApproval).toBe(true);
    expect(out.toolName).toBe("test.tool");
    expect(out.toolInput).toEqual({ x: 1 });
    expect(out.message).toContain("test.tool");
    // message 不锁中文 / 英文（CLAUDE.md §3 用户语言原则）
    expect(out.message).toMatch(/user's language|user language|their .* language/i);
    // 第一次调后 cache 应该有标记
    expect(cache.size()).toBe(1);
    store.clearAll();
    cache.clear();
  });

  it("ask 第二次调（同 input）→ cache 命中 → 放行调 execute (session bypass)", async () => {
    const { AskApprovalCache } = await import("../src/permissions/ask-cache.js");
    const cache = new AskApprovalCache(5_000);
    const runner = new HookRunner();
    const exec = vi.fn().mockResolvedValue({ ran: true });
    const tool = { id: "test.tool", execute: exec };
    const wrapped = withHooks(tool, {
      runner,
      permissionResolver: () => "ask",
      askCache: cache,
    });

    // 第一次调 → 被 ask 拦
    await wrapped.execute!({ x: 1, y: "abc" });
    expect(exec).not.toHaveBeenCalled();
    expect(cache.size()).toBe(1);

    // 第二次调（同 input）→ cache 命中 → 放行
    const out = await wrapped.execute!({ x: 1, y: "abc" });
    expect(exec).toHaveBeenCalledOnce();
    expect(exec).toHaveBeenCalledWith({ x: 1, y: "abc" }, undefined);
    expect(out).toEqual({ ran: true });
    // 一次性消费：第二次后 cache 又空了
    expect(cache.size()).toBe(0);
  });

  it("ask 第二次调（input 改了）→ cache 不命中 → 再 ask 一次", async () => {
    const { AskApprovalCache } = await import("../src/permissions/ask-cache.js");
    const cache = new AskApprovalCache(5_000);
    const runner = new HookRunner();
    const exec = vi.fn();
    const tool = { id: "test.tool", execute: exec };
    const wrapped = withHooks(tool, {
      runner,
      permissionResolver: () => "ask",
      askCache: cache,
    });

    await wrapped.execute!({ x: 1 });
    expect(cache.size()).toBe(1);

    // 第二次 input 不一样
    const out = (await wrapped.execute!({ x: 999 })) as { deniedBy: string };
    expect(exec).not.toHaveBeenCalled();
    expect(out.deniedBy).toBe("permission-ask");
    // 又 mark 了一条新的（首次的还在）→ size=2
    expect(cache.size()).toBe(2);
  });

  it("ask 第三次调（同 input）→ 仍要 ask（一次性消费 + 第二次已用掉）", async () => {
    const { AskApprovalCache } = await import("../src/permissions/ask-cache.js");
    const cache = new AskApprovalCache(5_000);
    const runner = new HookRunner();
    const exec = vi.fn().mockResolvedValue({ ok: 1 });
    const tool = { id: "test.tool", execute: exec };
    const wrapped = withHooks(tool, {
      runner,
      permissionResolver: () => "ask",
      askCache: cache,
    });

    await wrapped.execute!({ x: 1 }); // 第 1 次 → ask
    await wrapped.execute!({ x: 1 }); // 第 2 次 → 放行 + 消费
    expect(exec).toHaveBeenCalledOnce();

    // 第 3 次：cache 空，又被 ask
    const out = (await wrapped.execute!({ x: 1 })) as { deniedBy: string };
    expect(exec).toHaveBeenCalledOnce(); // 没增加
    expect(out.deniedBy).toBe("permission-ask");
  });

  it("ask 第一次调也把请求挂进 store (CLI / Web 备用入口)", async () => {
    const { PendingApprovalsStore } = await import(
      "../src/permissions/pending.js"
    );
    const { AskApprovalCache } = await import("../src/permissions/ask-cache.js");
    const store = new PendingApprovalsStore();
    const cache = new AskApprovalCache(5_000);
    const runner = new HookRunner();
    const tool = { id: "test.tool", execute: vi.fn() };
    const wrapped = withHooks(tool, {
      runner,
      permissionResolver: () => "ask",
      pendingApprovals: store,
      askCache: cache,
      askTimeoutMs: 5_000,
    });

    await wrapped.execute!({ y: 2 });
    expect(store.size()).toBe(1);
    const pending = store.list()[0]!;
    expect(pending.toolName).toBe("test.tool");
    expect(pending.toolInput).toEqual({ y: 2 });
    store.clearAll();
  });

  it("ask cache 用 stable key：input key 顺序不同也匹中 (DeepSeek 实测痛点)", async () => {
    const { AskApprovalCache } = await import("../src/permissions/ask-cache.js");
    const cache = new AskApprovalCache(5_000);
    const runner = new HookRunner();
    const exec = vi.fn().mockResolvedValue({ ok: 1 });
    const tool = { id: "test.tool", execute: exec };
    const wrapped = withHooks(tool, {
      runner,
      permissionResolver: () => "ask",
      askCache: cache,
    });

    // 第一次调：key 顺序 {candidateId, reason}
    await wrapped.execute!({ candidateId: "X", reason: "alpha" });
    expect(exec).not.toHaveBeenCalled();

    // 第二次调：LLM 把 key 顺序换成 {reason, candidateId} —— stable stringify 仍命中
    const out = await wrapped.execute!({ reason: "alpha", candidateId: "X" });
    expect(exec).toHaveBeenCalledOnce();
    expect(out).toEqual({ ok: 1 });
    cache.clear();
  });

  it("hook permissionOverride=allow wins over permissionResolver=deny", async () => {
    const runner = new HookRunner();
    const tool = makeTool(() => ({ ran: true }));
    runner.register({
      id: "force-allow",
      event: "PreToolUse",
      handler: () => ({ permissionOverride: "allow" }),
    });
    const wrapped = withHooks(tool, {
      runner,
      permissionResolver: () => "deny",
    });

    const out = await wrapped.execute!({});
    expect(out).toEqual({ ran: true });
  });

  it("execute throw → PostToolUseFailure event fires with isError", async () => {
    const runner = new HookRunner();
    const tool = makeTool(() => {
      throw new Error("boom");
    });
    const seen: { event: string; isError?: boolean }[] = [];
    runner.register({
      id: "watch-fail",
      event: "PostToolUseFailure",
      handler: (ctx) => {
        seen.push({ event: ctx.event, isError: ctx.isError });
      },
    });
    runner.register({
      id: "should-not-fire",
      event: "PostToolUse",
      handler: (ctx) => {
        seen.push({ event: ctx.event });
      },
    });
    const wrapped = withHooks(tool, { runner });

    const out = (await wrapped.execute!({})) as { isError: boolean };
    expect(out.isError).toBe(true);
    expect(seen).toEqual([{ event: "PostToolUseFailure", isError: true }]);
  });

  it("PostToolUse forceError=true flips success into error", async () => {
    const runner = new HookRunner();
    const tool = makeTool(() => ({ ok: true }));
    runner.register({
      id: "flip",
      event: "PostToolUse",
      handler: () => ({ forceError: true, message: "reconcile mismatch" }),
    });
    const wrapped = withHooks(tool, { runner });

    const out = (await wrapped.execute!({})) as { isError: boolean; output: unknown };
    expect(out.isError).toBe(true);
    // 原 output 仍然保留在 ``output`` 字段
    expect(out.output).toMatchObject({ ok: true, hookMessage: "reconcile mismatch" });
  });

  it("permissionResolver throw → middleware-error result (review B16)", async () => {
    const runner = new HookRunner();
    const tool = makeTool(() => ({ ok: true }));
    const wrapped = withHooks(tool, {
      runner,
      permissionResolver: () => {
        throw new Error("resolver bug");
      },
    });

    const out = (await wrapped.execute!({})) as {
      isError: boolean;
      deniedBy: string;
      message: string;
    };
    expect(out.isError).toBe(true);
    expect(out.deniedBy).toBe("middleware-error");
    expect(out.message).toContain("resolver bug");
  });

  it("hook message is prepended to dict output as hookMessage", async () => {
    const runner = new HookRunner();
    const tool = makeTool(() => ({ result: 42 }));
    runner.register({
      id: "note",
      event: "PostToolUse",
      handler: () => ({ message: "audit ok" }),
    });
    const wrapped = withHooks(tool, { runner });

    const out = (await wrapped.execute!({})) as { result: number; hookMessage: string };
    expect(out.result).toBe(42);
    expect(out.hookMessage).toBe("audit ok");
  });
});

// ────────────────────────────────────────────────────────────────────
// audit-log handler
// ────────────────────────────────────────────────────────────────────

describe("audit-log handler", () => {
  it("redacts sensitive keys (apiKey / approval_token)", async () => {
    const captured: Record<string, unknown>[] = [];
    const handler = createAuditLogHandler((r) => captured.push(r));

    await handler({
      event: "PostToolUse",
      toolName: "live.submit_order",
      toolInput: {
        symbol: "BTC/USDT",
        apiKey: "secret-key-123",
        nested: { approval_token: "tk-abc" },
      },
      isError: false,
    });

    expect(captured).toHaveLength(1);
    const rec = captured[0]!;
    const input = rec.input as Record<string, unknown>;
    expect(input.apiKey).toBe("[REDACTED]");
    expect((input.nested as Record<string, unknown>).approval_token).toBe("[REDACTED]");
    expect(input.symbol).toBe("BTC/USDT");
  });

  it("redacts PII keys (email / wallet_address / phone / ssn)", async () => {
    const captured: Record<string, unknown>[] = [];
    const handler = createAuditLogHandler((r) => captured.push(r));

    await handler({
      event: "PostToolUse",
      toolName: "trade.create_plan",
      toolInput: {
        email: "alice@example.com",
        walletAddress: "0xabcdef",
        nested: {
          phone_number: "+1-555-1234",
          ssn: "123-45-6789",
        },
        symbol: "BTC/USDT", // 不该 redact
      },
      isError: false,
    });

    const rec = captured[0]!;
    const input = rec.input as Record<string, unknown>;
    expect(input.email).toBe("[REDACTED]");
    expect(input.walletAddress).toBe("[REDACTED]");
    const nested = input.nested as Record<string, unknown>;
    expect(nested.phone_number).toBe("[REDACTED]");
    expect(nested.ssn).toBe("[REDACTED]");
    expect(input.symbol).toBe("BTC/USDT");
  });

  it("redaction is case-insensitive and underscore-insensitive", async () => {
    const captured: Record<string, unknown>[] = [];
    const handler = createAuditLogHandler((r) => captured.push(r));

    await handler({
      event: "PostToolUse",
      toolName: "x",
      toolInput: {
        API_KEY: "k1",       // 大写
        "api-key": "k2",      // 短横线
        Phone_Number: "p1",   // 大小写混合
      },
      isError: false,
    });

    const input = captured[0]!.input as Record<string, unknown>;
    expect(input.API_KEY).toBe("[REDACTED]");
    expect(input["api-key"]).toBe("[REDACTED]");
    expect(input.Phone_Number).toBe("[REDACTED]");
  });

  it("extraSensitiveKeys lets caller add custom fields", async () => {
    const captured: Record<string, unknown>[] = [];
    const handler = createAuditLogHandler({
      sink: (r) => captured.push(r),
      extraSensitiveKeys: ["internalNote", "client_id"],
    });

    await handler({
      event: "PostToolUse",
      toolName: "x",
      toolInput: { internalNote: "secret", client_id: "c-1", symbol: "BTC/USDT" },
      isError: false,
    });

    const input = captured[0]!.input as Record<string, unknown>;
    expect(input.internalNote).toBe("[REDACTED]");
    expect(input.client_id).toBe("[REDACTED]");
    expect(input.symbol).toBe("BTC/USDT");
  });

  it("defaultAuditRegistration registers non-blocking PostToolUse hook", () => {
    const reg = defaultAuditRegistration();
    expect(reg.event).toBe("PostToolUse");
    expect(reg.blocking).toBe(false);
    expect(reg.matcher).toContain("paper.*");
    expect(reg.matcher).toContain("live.*");
  });
});

// ────────────────────────────────────────────────────────────────────
// defaultGetSessionId · Mastra runtime context 字段抽取
// ────────────────────────────────────────────────────────────────────

describe("defaultGetSessionId", () => {
  it("prefers threadId (Mastra native)", () => {
    expect(defaultGetSessionId({ threadId: "t-1", runId: "r-1" })).toBe("t-1");
  });

  it("falls back to runId when no threadId", () => {
    expect(defaultGetSessionId({ runId: "r-1" })).toBe("r-1");
  });

  it("falls back to requestContext.sessionId", () => {
    expect(defaultGetSessionId({ requestContext: { sessionId: "s-1" } })).toBe("s-1");
  });

  it("falls back to top-level sessionId (test ctx)", () => {
    expect(defaultGetSessionId({ sessionId: "s-2" })).toBe("s-2");
  });

  it("returns undefined for empty / malformed ctx", () => {
    expect(defaultGetSessionId(undefined)).toBeUndefined();
    expect(defaultGetSessionId(null)).toBeUndefined();
    expect(defaultGetSessionId({})).toBeUndefined();
    expect(defaultGetSessionId({ threadId: "" })).toBeUndefined();
    expect(defaultGetSessionId({ threadId: 123 })).toBeUndefined();
  });

  it("withHooks wires defaultGetSessionId into HookContext by default", async () => {
    const runner = new HookRunner();
    let seenSessionId: string | undefined;
    runner.register({
      id: "spy",
      event: "PreToolUse",
      handler: (ctx) => {
        seenSessionId = ctx.sessionId;
      },
    });
    const tool = { id: "t", description: "", execute: async () => ({}) };
    const wrapped = withHooks(tool, { runner });

    await wrapped.execute!({}, { threadId: "thr-42" });
    expect(seenSessionId).toBe("thr-42");
  });
});

// ────────────────────────────────────────────────────────────────────
// grid-size-cap (ADR-0025 §D4)
// ────────────────────────────────────────────────────────────────────

describe("grid-size-cap", () => {
  const handler = createGridSizeCapHandler();

  it("allows grid under default max (20)", async () => {
    const r = await handler({
      event: "PreToolUse",
      toolName: "swarm.run_backtest_grid",
      toolInput: { strategies: ["a", "b", "c"], symbols: ["BTC", "ETH"] },
    });
    expect(r).toBeUndefined();
  });

  it("allows boundary case (exactly max)", async () => {
    // 4 × 5 = 20
    const r = await handler({
      event: "PreToolUse",
      toolName: "swarm.run_backtest_grid",
      toolInput: {
        strategies: ["a", "b", "c", "d"],
        symbols: ["BTC", "ETH", "SOL", "BNB", "AVAX"],
      },
    });
    expect(r).toBeUndefined();
  });

  it("denies grid > max with explicit reason mentioning both dims", async () => {
    // 5 × 5 = 25 > 20
    const r = await handler({
      event: "PreToolUse",
      toolName: "swarm.run_backtest_grid",
      toolInput: {
        strategies: ["a", "b", "c", "d", "e"],
        symbols: ["BTC", "ETH", "SOL", "BNB", "AVAX"],
      },
    });
    expect(r).toMatchObject({ permissionOverride: "deny" });
    const msg = (r as { message: string }).message;
    expect(msg).toMatch(/25/);
    expect(msg).toMatch(/5 strategies/);
    expect(msg).toMatch(/5 symbols/);
    expect(msg).toMatch(/20/); // 含上限值
  });

  it("custom max override (smaller cap for stricter env)", async () => {
    const strict = createGridSizeCapHandler({ max: 6 });
    // 3 × 3 = 9 > 6
    const r = await strict({
      event: "PreToolUse",
      toolName: "swarm.run_backtest_grid",
      toolInput: { strategies: ["a", "b", "c"], symbols: ["x", "y", "z"] },
    });
    expect(r).toMatchObject({ permissionOverride: "deny" });
  });

  it("ignores missing fields (lets schema layer surface the real error)", async () => {
    // strategies 缺失 → 不在 hook 层 deny，留给 zod 报清晰错误
    const r = await handler({
      event: "PreToolUse",
      toolName: "swarm.run_backtest_grid",
      toolInput: { symbols: ["BTC"] },
    });
    expect(r).toBeUndefined();
  });

  it("ignores non-array values (e.g. LLM hallucinated string)", async () => {
    const r = await handler({
      event: "PreToolUse",
      toolName: "swarm.run_backtest_grid",
      toolInput: { strategies: "sma_cross", symbols: ["BTC"] },
    });
    expect(r).toBeUndefined();
  });

  it("default registration matches only swarm.run_backtest_grid", () => {
    const reg = defaultGridSizeCapRegistration();
    expect(reg.event).toBe("PreToolUse");
    expect(reg.matcher).toBe("swarm.run_backtest_grid");
    expect(reg.blocking).toBe(true);
    expect(DEFAULT_GRID_MAX).toBe(20);
  });
});

// ────────────────────────────────────────────────────────────────────
// tool-idempotency（fix ADR-0025 LLM 重复 swarm 调用）
// ────────────────────────────────────────────────────────────────────

describe("tool-idempotency", () => {
  it("post caches successful output, pre denies repeat with same input", async () => {
    const { pre, post, cache } = createToolIdempotencyHandlers();

    // 第一次：cache miss → pre 不拦
    const r1 = await pre({
      event: "PreToolUse",
      sessionId: "s-1",
      toolName: "swarm.run_backtest_grid",
      toolInput: { strategies: ["sma"], symbols: ["BTC/USDT"] },
    });
    expect(r1).toBeUndefined();

    // post 记 cache
    await post({
      event: "PostToolUse",
      sessionId: "s-1",
      toolName: "swarm.run_backtest_grid",
      toolInput: { strategies: ["sma"], symbols: ["BTC/USDT"] },
      toolOutput: { summary: { ok: 1, total: 1 } },
      isError: false,
    });
    expect(cache.size).toBe(1);

    // 第二次：cache hit → pre deny
    const r2 = await pre({
      event: "PreToolUse",
      sessionId: "s-1",
      toolName: "swarm.run_backtest_grid",
      toolInput: { strategies: ["sma"], symbols: ["BTC/USDT"] },
    });
    expect(r2).toMatchObject({ permissionOverride: "deny" });
    const msg = (r2 as { message: string }).message;
    expect(msg).toMatch(/IDEMPOTENT_DUP/);
    expect(msg).toMatch(/previous_result/);
    // 摘要包含 ok: 1
    expect(msg).toMatch(/ok"?:\s*1/);
  });

  it("different sessionId → no cache hit (per-session isolation)", async () => {
    const { pre, post } = createToolIdempotencyHandlers();
    const input = { x: 1 };

    await post({
      event: "PostToolUse",
      sessionId: "s-A",
      toolName: "swarm.x",
      toolInput: input,
      toolOutput: { ok: true },
      isError: false,
    });
    const r = await pre({
      event: "PreToolUse",
      sessionId: "s-B",
      toolName: "swarm.x",
      toolInput: input,
    });
    expect(r).toBeUndefined();
  });

  it("different input (key order swap) still hits — stableStringify keys are sorted", async () => {
    const { pre, post } = createToolIdempotencyHandlers();

    await post({
      event: "PostToolUse",
      sessionId: "s-1",
      toolName: "swarm.x",
      toolInput: { a: 1, b: 2 },
      toolOutput: { ok: true },
      isError: false,
    });
    // 改变 key 顺序，仍应命中（stable stringify）
    const r = await pre({
      event: "PreToolUse",
      sessionId: "s-1",
      toolName: "swarm.x",
      toolInput: { b: 2, a: 1 },
    });
    expect(r).toMatchObject({ permissionOverride: "deny" });
  });

  it("does NOT cache errors (failed call → next call still executes)", async () => {
    const { pre, post, cache } = createToolIdempotencyHandlers();

    await post({
      event: "PostToolUseFailure",
      sessionId: "s-1",
      toolName: "swarm.x",
      toolInput: { x: 1 },
      toolOutput: { code: "BOOM" },
      isError: true,
    });
    expect(cache.size).toBe(0);

    const r = await pre({
      event: "PreToolUse",
      sessionId: "s-1",
      toolName: "swarm.x",
      toolInput: { x: 1 },
    });
    expect(r).toBeUndefined();
  });

  it("TTL expiration purges cache entry", async () => {
    const { pre, post } = createToolIdempotencyHandlers({ ttlMs: 1 });

    await post({
      event: "PostToolUse",
      sessionId: "s-1",
      toolName: "swarm.x",
      toolInput: { x: 1 },
      toolOutput: { ok: true },
      isError: false,
    });
    await new Promise((r) => setTimeout(r, 10));
    const r = await pre({
      event: "PreToolUse",
      sessionId: "s-1",
      toolName: "swarm.x",
      toolInput: { x: 1 },
    });
    expect(r).toBeUndefined();
  });

  it("end-to-end via withHooks: 2nd identical call returns isError + IDEMPOTENT_DUP", async () => {
    const runner = new HookRunner();
    const idem = defaultIdempotencyRegistrations();
    runner.register(idem.pre);
    runner.register(idem.post);

    let executeCount = 0;
    const tool = {
      id: "swarm.run_backtest_grid",
      description: "",
      execute: async (_input: unknown) => {
        executeCount++;
        return { summary: { ok: 1 } };
      },
    };
    const wrapped = withHooks(tool, { runner });
    const input = { strategies: ["sma"], symbols: ["BTC/USDT"] };
    const ctx = { threadId: "thr-1" };

    const r1 = await wrapped.execute!(input, ctx);
    expect(executeCount).toBe(1);
    expect(r1).toMatchObject({ summary: { ok: 1 } });

    // 二次同 input → 不再执行真 tool；返回 isError + idempotent 消息
    const r2 = (await wrapped.execute!(input, ctx)) as { isError: boolean; message: string };
    expect(executeCount).toBe(1); // 没再 +1
    expect(r2.isError).toBe(true);
    expect(r2.message).toMatch(/IDEMPOTENT_DUP/);
  });

  it("default registration: matcher swarm.*, pre blocking, post non-blocking", () => {
    const { pre, post } = defaultIdempotencyRegistrations();
    expect(pre.event).toBe("PreToolUse");
    expect(pre.matcher).toBe("swarm.*");
    expect(pre.blocking).toBe(true);
    expect(post.event).toBe("PostToolUse");
    expect(post.matcher).toBe("swarm.*");
    expect(post.blocking).toBe(false);
  });
});
