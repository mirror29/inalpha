/**
 * Stop hook 3 个 handler + StopHookRunner 单测（ADR-0010 §Stop hook 补丁）。
 *
 * handler 都是纯函数（注入式 fetcher），测试给固定 fixture 即可，不依赖 mastra
 * 或 paper service 真实连接。
 */
import { describe, expect, it, vi } from "vitest";

import {
  HookRunner,
  StopHookRunner,
  createAnalystQuorumCheckHandler,
  createFillReconcileCheckHandler,
  createPendingPlanCheckHandler,
  formatStopNotice,
} from "../src/hooks/index.js";

// ────────────────────────────────────────────────────────────────────
// pending-plan-check
// ────────────────────────────────────────────────────────────────────

describe("pending-plan-check handler", () => {
  it("noop when no fetcher injected", async () => {
    const h = createPendingPlanCheckHandler();
    const d = await h({ event: "Stop" });
    expect(d).toEqual({});
  });

  it("noop when fetcher returns empty", async () => {
    const h = createPendingPlanCheckHandler({
      fetcher: async () => [],
    });
    const d = await h({ event: "Stop", sessionId: "s1" });
    expect(d).toEqual({});
  });

  it("force continue when plans exist; reason includes count + ids", async () => {
    const h = createPendingPlanCheckHandler({
      fetcher: async () => [
        { plan_id: "p1", status: "approved", symbol: "BTC/USDT" },
        { plan_id: "p2", status: "pending_approval", symbol: "ETH/USDT" },
      ],
    });
    const d = await h({ event: "Stop", sessionId: "s1" });
    expect(d).toMatchObject({ continue: false });
    expect((d as { reason: string }).reason).toContain("2");
    expect((d as { reason: string }).reason).toContain("p1");
    expect((d as { reason: string }).reason).toContain("p2");
  });

  it("does not throw when fetcher errors; returns noop", async () => {
    const h = createPendingPlanCheckHandler({
      fetcher: async () => {
        throw new Error("paper down");
      },
    });
    const d = await h({ event: "Stop" });
    expect(d).toEqual({});
  });
});

// ────────────────────────────────────────────────────────────────────
// fill-reconcile-check
// ────────────────────────────────────────────────────────────────────

describe("fill-reconcile-check handler", () => {
  it("noop when no fetcher", async () => {
    const h = createFillReconcileCheckHandler();
    expect(await h({ event: "Stop" })).toEqual({});
  });

  it("ignores orders younger than minAgeSeconds", async () => {
    const h = createFillReconcileCheckHandler({
      fetcher: async () => [
        { client_order_id: "o1", symbol: "BTC/USDT", venue: "binance", age_seconds: 10 },
      ],
      minAgeSeconds: 30,
    });
    expect(await h({ event: "Stop" })).toEqual({});
  });

  it("force continue when any order >= minAgeSeconds", async () => {
    const h = createFillReconcileCheckHandler({
      fetcher: async () => [
        { client_order_id: "o1", symbol: "BTC/USDT", venue: "binance", age_seconds: 5 },
        { client_order_id: "o2", symbol: "ETH/USDT", venue: "binance", age_seconds: 60 },
      ],
      minAgeSeconds: 30,
    });
    const d = await h({ event: "Stop" });
    expect(d).toMatchObject({ continue: false });
    expect((d as { reason: string }).reason).toContain("o2");
    // 年轻订单不进 reason
    expect((d as { reason: string }).reason).not.toContain("o1");
  });

  it("fetcher throw → noop", async () => {
    const h = createFillReconcileCheckHandler({
      fetcher: async () => {
        throw new Error("paper down");
      },
    });
    expect(await h({ event: "Stop" })).toEqual({});
  });
});

// ────────────────────────────────────────────────────────────────────
// analyst-quorum-check
// ────────────────────────────────────────────────────────────────────

describe("analyst-quorum-check handler", () => {
  it("noop when no fetcher", async () => {
    const h = createAnalystQuorumCheckHandler();
    expect(await h({ event: "Stop" })).toEqual({});
  });

  it("noop when no prior research (fetcher returns null)", async () => {
    const h = createAnalystQuorumCheckHandler({
      fetcher: async () => null,
    });
    expect(await h({ event: "Stop" })).toEqual({});
  });

  it("noop when quorum met (>= minQuorum analysts with confidence>0)", async () => {
    const h = createAnalystQuorumCheckHandler({
      fetcher: async () => ({
        briefs: [
          { analyst: "technical", confidence: 0.7 },
          { analyst: "fundamental", confidence: 0.5 },
          { analyst: "sentiment", confidence: 0.6 },
          { analyst: "risk", confidence: 0.0 }, // 失败的不算
          { analyst: "macro", confidence: 0.8 },
        ],
      }),
      minQuorum: 3,
    });
    expect(await h({ event: "Stop" })).toEqual({});
  });

  it("force continue when usable analysts < quorum", async () => {
    const h = createAnalystQuorumCheckHandler({
      fetcher: async () => ({
        briefs: [
          { analyst: "technical", confidence: 0.5 },
          { analyst: "fundamental", confidence: 0.0 }, // 失败
          { analyst: "sentiment", confidence: 0.0 },
          { analyst: "risk", confidence: 0.0 },
          { analyst: "macro", confidence: 0.0 },
        ],
      }),
      minQuorum: 3,
    });
    const d = await h({ event: "Stop" });
    expect(d).toMatchObject({ continue: false });
    expect((d as { reason: string }).reason).toContain("1"); // found=1
    expect((d as { reason: string }).reason).toContain("3"); // quorum=3
  });

  it("fetcher throw → noop", async () => {
    const h = createAnalystQuorumCheckHandler({
      fetcher: async () => {
        throw new Error("session store down");
      },
    });
    expect(await h({ event: "Stop" })).toEqual({});
  });
});

// ────────────────────────────────────────────────────────────────────
// StopHookRunner（max_force_continue 限流）
// ────────────────────────────────────────────────────────────────────

describe("StopHookRunner · max_force_continue 防 thrashing", () => {
  function buildRunnerWithBlocker(): HookRunner {
    const r = new HookRunner();
    r.register({
      id: "always-block",
      event: "Stop",
      handler: () => ({ continue: false, reason: "still work to do" }),
    });
    return r;
  }

  it("first call: shouldContinue=true with reason", async () => {
    const sr = new StopHookRunner(buildRunnerWithBlocker(), 3);
    const d = await sr.maybeForceContinue({ sessionId: "s1" });
    expect(d.shouldContinue).toBe(true);
    expect(d.reason).toContain("still work");
    expect(d.forceCount).toBe(1);
  });

  it("after maxForceContinue, lets turn end despite hook saying continue", async () => {
    const sr = new StopHookRunner(buildRunnerWithBlocker(), 3);
    // 触 3 次 → 计数 3 → 仍 continue
    await sr.maybeForceContinue({ sessionId: "s1" });
    await sr.maybeForceContinue({ sessionId: "s1" });
    const third = await sr.maybeForceContinue({ sessionId: "s1" });
    expect(third.shouldContinue).toBe(true);
    expect(third.forceCount).toBe(3);
    // 第 4 次 hook 还说 continue，但 runner 不放行
    const fourth = await sr.maybeForceContinue({ sessionId: "s1" });
    expect(fourth.shouldContinue).toBe(false);
    expect(fourth.forceCount).toBe(3);
  });

  it("session count is per-sessionId", async () => {
    const sr = new StopHookRunner(buildRunnerWithBlocker(), 1);
    await sr.maybeForceContinue({ sessionId: "alice" });
    const a2 = await sr.maybeForceContinue({ sessionId: "alice" });
    expect(a2.shouldContinue).toBe(false); // alice 已达上限
    // bob 仍可强 continue
    const b = await sr.maybeForceContinue({ sessionId: "bob" });
    expect(b.shouldContinue).toBe(true);
  });

  it("when no hook says continue, returns shouldContinue=false (normal stop)", async () => {
    const empty = new HookRunner();
    const sr = new StopHookRunner(empty, 3);
    const d = await sr.maybeForceContinue({ sessionId: "s1" });
    expect(d.shouldContinue).toBe(false);
    expect(d.reason).toBeNull();
  });

  it("successful stop resets the session's force count", async () => {
    const r = new HookRunner();
    let on = true;
    r.register({
      id: "toggle",
      event: "Stop",
      handler: () => (on ? { continue: false, reason: "x" } : {}),
    });
    const sr = new StopHookRunner(r, 3);

    // 强 continue 2 次
    await sr.maybeForceContinue({ sessionId: "s1" });
    await sr.maybeForceContinue({ sessionId: "s1" });
    // 关掉 hook → 正常 stop → 计数清零
    on = false;
    const normal = await sr.maybeForceContinue({ sessionId: "s1" });
    expect(normal.shouldContinue).toBe(false);

    // 再开 hook → 计数从 1 重新计
    on = true;
    const next = await sr.maybeForceContinue({ sessionId: "s1" });
    expect(next.shouldContinue).toBe(true);
    expect(next.forceCount).toBe(1);
  });

  it("resetSession clears count manually", async () => {
    const sr = new StopHookRunner(buildRunnerWithBlocker(), 1);
    await sr.maybeForceContinue({ sessionId: "s1" });
    sr.resetSession("s1");
    const r2 = await sr.maybeForceContinue({ sessionId: "s1" });
    expect(r2.shouldContinue).toBe(true); // 又能 force 了
    expect(r2.forceCount).toBe(1);
  });

  it("Stop hook handler throw (blocking=true) → propagates as deny → no continue", async () => {
    const r = new HookRunner();
    r.register({
      id: "boom",
      event: "Stop",
      handler: () => {
        throw new Error("oops");
      },
      blocking: true,
    });
    const sr = new StopHookRunner(r);
    const d = await sr.maybeForceContinue({ sessionId: "s1" });
    // HookRunner blocking error → permissionOverride='deny' + 不带 continue=false
    // StopHookRunner 看不到 continue 标志 → 视作正常结束
    expect(d.shouldContinue).toBe(false);
  });
});

// ────────────────────────────────────────────────────────────────────
// formatStopNotice
// ────────────────────────────────────────────────────────────────────

describe("formatStopNotice", () => {
  it("prepends [system_notice] marker per ADR-0010 §关键约定 6", () => {
    expect(formatStopNotice("plan p1 still pending")).toBe(
      "[system_notice] plan p1 still pending",
    );
  });
});

// ────────────────────────────────────────────────────────────────────
// 集成 smoke：3 handler 注册到 runner，跑 maybeForceContinue
// ────────────────────────────────────────────────────────────────────

describe("StopHookRunner · 3 handler 注册 + 联合决策", () => {
  it("any single handler wanting continue → runner says continue", async () => {
    const r = new HookRunner();
    r.register({
      id: "pending",
      event: "Stop",
      handler: createPendingPlanCheckHandler({
        fetcher: async () => [
          { plan_id: "p1", status: "approved", symbol: "BTC/USDT" },
        ],
      }),
    });
    r.register({
      id: "reconcile",
      event: "Stop",
      handler: createFillReconcileCheckHandler({
        fetcher: async () => [],
      }),
    });
    r.register({
      id: "quorum",
      event: "Stop",
      handler: createAnalystQuorumCheckHandler({
        fetcher: async () => null,
      }),
    });

    const sr = new StopHookRunner(r);
    const d = await sr.maybeForceContinue({ sessionId: "s1" });
    expect(d.shouldContinue).toBe(true);
    expect(d.appliedHookIds).toContain("pending");
    // 模板已改语言中立机器状态行（§3），断言结构关键字而非英文散文
    expect(d.reason ?? "").toContain("pending_plans");
  });

  it("all three clean → shouldContinue=false", async () => {
    const r = new HookRunner();
    const noopFetcher = vi.fn(async () => []);
    r.register({
      id: "pending",
      event: "Stop",
      handler: createPendingPlanCheckHandler({ fetcher: noopFetcher }),
    });
    r.register({
      id: "reconcile",
      event: "Stop",
      handler: createFillReconcileCheckHandler({ fetcher: noopFetcher }),
    });
    r.register({
      id: "quorum",
      event: "Stop",
      handler: createAnalystQuorumCheckHandler({
        fetcher: async () => null,
      }),
    });
    const sr = new StopHookRunner(r);
    const d = await sr.maybeForceContinue({ sessionId: "s1" });
    expect(d.shouldContinue).toBe(false);
  });
});
