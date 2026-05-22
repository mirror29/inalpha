/**
 * Hooks 层单测：matcher / runner / with-hooks 三个模块的核心行为。
 *
 * 不依赖 Mastra runtime —— withHooks 接受任意"鸭子 tool"（id + execute），可
 * 直接构造测试 fixture。
 */
import { describe, expect, it, vi } from "vitest";

import {
  HookRunner,
  toolMatches,
  withHooks,
  defaultAuditRegistration,
  createAuditLogHandler,
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

  it("permissionResolver=ask returns pending-approval marker", async () => {
    const runner = new HookRunner();
    const exec = vi.fn();
    const tool = { id: "test.tool", description: "", execute: exec };
    const wrapped = withHooks(tool, {
      runner,
      permissionResolver: () => "ask",
    });

    const out = (await wrapped.execute!({})) as { isError: boolean; deniedBy: string };
    expect(exec).not.toHaveBeenCalled();
    expect(out.isError).toBe(true);
    expect(out.deniedBy).toBe("permission-ask-pending");
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

  it("defaultAuditRegistration registers non-blocking PostToolUse hook", () => {
    const reg = defaultAuditRegistration();
    expect(reg.event).toBe("PostToolUse");
    expect(reg.blocking).toBe(false);
    expect(reg.matcher).toContain("paper.*");
    expect(reg.matcher).toContain("live.*");
  });
});
