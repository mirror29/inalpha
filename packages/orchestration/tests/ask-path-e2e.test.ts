/**
 * Ask-path 集成测试（ADR-0018 D-9.1b 修订）。
 *
 * 与 `hooks.test.ts` 的单元拆分不同——本文件用**完整组合**（真 withHooks + 真
 * AskApprovalCache + 真 PendingApprovalsStore + 注入 telemetry sink）覆盖一次
 * ask 流程的所有可观察事件 + 跨 session 隔离 + TTL 自然过期。
 *
 * 覆盖的事件序列（events emitted to telemetry sink）：
 *
 * - 第一次 ask 拦截：``ask_marked``（cache.mark）+ ``ask_pending_requested``（store.request）
 * - 第二次 ask 重调（cache hit）：``ask_consumed``
 * - 用户拒绝 / cache 自然过期：``ask_cache_expired``
 * - store.request 30s timeout fire-and-forget：``ask_pending_resolved` via=timeout
 * - 跨 sessionId 不复用
 *
 * 不覆盖（已在 hooks.test.ts / permissions-pending.test.ts 单测）：
 * - matcher / runner 自身逻辑
 * - HTTP API endpoint（permissions-pending.test.ts §permissionsApiRoutes）
 */
import { describe, expect, it, vi } from "vitest";

import { HookRunner, withHooks } from "../src/hooks/index.js";
import { AskApprovalCache } from "../src/permissions/ask-cache.js";
import { PendingApprovalsStore } from "../src/permissions/pending.js";

function makeEnv(opts: { cacheTtlMs?: number } = {}) {
  const events: Array<Record<string, unknown>> = [];
  const sink = (r: Record<string, unknown>) => {
    events.push(r);
  };
  const cache = new AskApprovalCache(opts.cacheTtlMs ?? 60_000, sink);
  const store = new PendingApprovalsStore(sink);
  const runner = new HookRunner();
  return { events, cache, store, runner };
}

function eventsOf(
  events: Array<Record<string, unknown>>,
  name: string,
): Array<Record<string, unknown>> {
  return events.filter((e) => e.event === name);
}

describe("ask-path e2e · 完整 happy path + telemetry 事件序列", () => {
  it("第一次 ask 触发 mark + pending_requested；第二次重调 cache hit + consumed → execute", async () => {
    const { events, cache, store, runner } = makeEnv();
    const exec = vi.fn().mockResolvedValue({ candidateId: "c-42", status: "promoted" });
    const tool = { id: "paper.promote_candidate", description: "", execute: exec };
    const wrapped = withHooks(tool, {
      runner,
      permissionResolver: () => "ask",
      askCache: cache,
      pendingApprovals: store,
      getSessionId: () => "thread-A",
    });

    // 第一次：被拦
    const first = (await wrapped.execute!({ candidateId: "c-42" })) as {
      isError: boolean;
      requiresApproval: boolean;
    };
    expect(first.isError).toBe(true);
    expect(first.requiresApproval).toBe(true);
    expect(exec).not.toHaveBeenCalled();

    // 第一次的 telemetry：先 mark 再 pending_requested（顺序由 with-hooks 决定）
    expect(eventsOf(events, "ask_marked")).toHaveLength(1);
    expect(eventsOf(events, "ask_pending_requested")).toHaveLength(1);
    const marked = eventsOf(events, "ask_marked")[0];
    expect(marked).toMatchObject({
      toolName: "paper.promote_candidate",
      sessionId: "thread-A",
    });

    // 第二次：同 input → cache hit → 真 execute
    const second = await wrapped.execute!({ candidateId: "c-42" });
    expect(exec).toHaveBeenCalledOnce();
    expect(second).toEqual({ candidateId: "c-42", status: "promoted" });

    // 第二次的 telemetry：ask_consumed
    expect(eventsOf(events, "ask_consumed")).toHaveLength(1);
    const consumed = eventsOf(events, "ask_consumed")[0];
    expect(consumed).toMatchObject({
      toolName: "paper.promote_candidate",
      sessionId: "thread-A",
    });
    expect(typeof consumed.latency_ms).toBe("number");

    // 副作用 cleanup：cache 一次性消费
    expect(cache.size()).toBe(0);
    store.clearAll();
  });
});

describe("ask-path e2e · 跨 sessionId 不复用", () => {
  it("A 用户 mark 不被 B 用户 consume；两条独立 entry", async () => {
    const { events, cache, store, runner } = makeEnv();
    const exec = vi.fn();
    const tool = { id: "paper.promote_candidate", description: "", execute: exec };

    // sessionId 由 ctx 提供，每次 invoke 时 ctx.agent.threadId 不同
    const wrapped = withHooks(tool, {
      runner,
      permissionResolver: () => "ask",
      askCache: cache,
      pendingApprovals: store,
    });

    // A 用户拦一次
    await wrapped.execute!({ candidateId: "c-42" }, { agent: { threadId: "thread-A" } });
    expect(cache.size()).toBe(1);

    // B 用户**同 input** 重调 → cache 不命中（不同 sessionId）→ 也被拦
    const bOut = (await wrapped.execute!(
      { candidateId: "c-42" },
      { agent: { threadId: "thread-B" } },
    )) as { isError: boolean; requiresApproval: boolean };
    expect(bOut.requiresApproval).toBe(true);
    expect(exec).not.toHaveBeenCalled();
    // 两条独立 entry：A + B 各一
    expect(cache.size()).toBe(2);

    // telemetry：两次 ask_marked，sessionId 不同
    const marks = eventsOf(events, "ask_marked");
    expect(marks).toHaveLength(2);
    const sids = new Set(marks.map((e) => e.sessionId));
    expect(sids).toEqual(new Set(["thread-A", "thread-B"]));

    store.clearAll();
    cache.clear();
  });
});

describe("ask-path e2e · cache TTL 自然过期", () => {
  it("用户不重调时 cache TTL 到期触发 expired 事件", async () => {
    const { events, cache, store, runner } = makeEnv({ cacheTtlMs: 50 });
    const exec = vi.fn();
    const tool = { id: "paper.promote_candidate", description: "", execute: exec };
    const wrapped = withHooks(tool, {
      runner,
      permissionResolver: () => "ask",
      askCache: cache,
      pendingApprovals: store,
      getSessionId: () => "thread-A",
    });

    await wrapped.execute!({ candidateId: "c-42" });
    expect(cache.size()).toBe(1);
    expect(eventsOf(events, "ask_marked")).toHaveLength(1);

    // 等 TTL 过期（用真 setTimeout，与项目其他测试一致）
    await new Promise((r) => setTimeout(r, 80));

    // cache 已被自动清理
    expect(cache.size()).toBe(0);
    // telemetry：收到 ask_cache_expired
    expect(eventsOf(events, "ask_cache_expired")).toHaveLength(1);
    expect(eventsOf(events, "ask_cache_expired")[0]).toMatchObject({
      toolName: "paper.promote_candidate",
      sessionId: "thread-A",
    });

    store.clearAll();
  });
});

describe("ask-path e2e · store.request fire-and-forget timeout", () => {
  it("第一次 ask 后 30s timeout 触发 pending_resolved via=timeout（不影响 cache）", async () => {
    const { events, cache, store, runner } = makeEnv();
    const exec = vi.fn();
    const tool = { id: "paper.promote_candidate", description: "", execute: exec };
    const wrapped = withHooks(tool, {
      runner,
      permissionResolver: () => "ask",
      askCache: cache,
      pendingApprovals: store,
      askTimeoutMs: 30, // 用小超时让测试快
      getSessionId: () => "thread-A",
    });

    await wrapped.execute!({ candidateId: "c-42" });
    expect(eventsOf(events, "ask_pending_requested")).toHaveLength(1);

    // 等 store timeout 触发（与 cache TTL 独立）
    await new Promise((r) => setTimeout(r, 60));

    const resolved = eventsOf(events, "ask_pending_resolved");
    expect(resolved).toHaveLength(1);
    expect(resolved[0]).toMatchObject({
      toolName: "paper.promote_candidate",
      sessionId: "thread-A",
      decision: "deny",
      via: "timeout",
    });
    // cache 仍存活（60s TTL 默认未到）—— 印证 store timeout 不影响 cache 路径
    expect(cache.size()).toBe(1);

    cache.clear();
  });
});

describe("ask-path e2e · 用户拒绝路径", () => {
  it("agent 不重调 + cache 60s TTL 过期 → 后续相同 input 重新触发 ask（不复用旧 mark）", async () => {
    const { events, cache, store, runner } = makeEnv({ cacheTtlMs: 50 });
    const exec = vi.fn();
    const tool = { id: "paper.promote_candidate", description: "", execute: exec };
    const wrapped = withHooks(tool, {
      runner,
      permissionResolver: () => "ask",
      askCache: cache,
      pendingApprovals: store,
      getSessionId: () => "thread-A",
    });

    // 第一次 ask
    await wrapped.execute!({ candidateId: "c-42" });
    expect(eventsOf(events, "ask_marked")).toHaveLength(1);

    // 模拟"用户拒绝 → agent 不重调" —— 啥都不做，等 cache 过期
    await new Promise((r) => setTimeout(r, 80));
    expect(eventsOf(events, "ask_cache_expired")).toHaveLength(1);
    expect(cache.size()).toBe(0);

    // 用户改变主意，agent 重新发起同样 tool 调用 → 应再走一遍 ask（不复用旧 mark）
    await wrapped.execute!({ candidateId: "c-42" });
    expect(eventsOf(events, "ask_marked")).toHaveLength(2); // 新一轮 mark
    expect(exec).not.toHaveBeenCalled();

    store.clearAll();
    cache.clear();
  });
});
