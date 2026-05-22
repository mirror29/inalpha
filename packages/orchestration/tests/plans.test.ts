/**
 * PlanStore + trade-plan tool 单测。
 *
 * 范围：
 *
 * - PlanStore 状态机（create → approve → consumeApproval → recordExecution）
 * - token 一次性 + 过期判定
 * - tool 包装层把 PlanError 翻成 ``{ ok: false, code }`` 返回
 * - executeTradePlanTool 走 paper /orders/submit（mock fetch）
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { clearSettings, setSettings } from "../src/config.js";
import { PlanError, createPlanStore } from "../src/plans/store.js";
import {
  approveTradePlanTool,
  createTradePlanTool,
  executeTradePlanTool,
  getTradePlanTool,
  rejectTradePlanTool,
} from "../src/tools/trade-plan.js";
import { planStore } from "../src/plans/store.js";

const TEST_TOKEN = "test-token-doesnt-need-to-be-real";

beforeEach(() => {
  setSettings({
    dataServiceUrl: "http://data-mock.test",
    paperServiceUrl: "http://paper-mock.test",
    researchServiceUrl: "http://research-mock.test",
    jwtSecret: "test-secret-32-chars-or-more-xxxxxxx",
    jwtAlgorithm: "HS256",
  });
  // tool 用 module-level planStore；每个测试前清干净
  planStore.clear();
});

afterEach(() => {
  clearSettings();
  vi.restoreAllMocks();
  planStore.clear();
});

const ctx = (authToken: string | undefined = TEST_TOKEN): never =>
  ({ requestContext: { authToken } }) as never;

function mockFetch(impl: (url: string, init?: RequestInit) => Promise<Response>) {
  vi.stubGlobal("fetch", vi.fn(impl));
}

// ────────────────────────────────────────────────────────────────────
// PlanStore unit tests（纯状态机，不依赖 tool 层）
// ────────────────────────────────────────────────────────────────────

describe("PlanStore state machine", () => {
  it("create → approve → consumeApproval → recordExecution happy path", () => {
    const store = createPlanStore();
    const plan = store.create({
      intent: "open_long",
      symbol: "BTC/USDT",
      orderParams: { side: "BUY", type: "MARKET", quantity: 0.01 },
      rationale: "测试用例：短线突破",
    });
    expect(plan.status).toBe("pending_approval");
    expect(plan.approvalToken).toBeNull();

    const approval = store.approve({ planId: plan.planId, approver: "risk-agent" });
    expect(approval.status).toBe("approved");
    expect(approval.approvalToken).toBeTruthy();

    const consumed = store.consumeApproval(plan.planId, approval.approvalToken);
    // token 立刻作废
    expect(consumed.approvalToken).toBeNull();
    expect(consumed.status).toBe("approved"); // 还没 record_execution

    const executed = store.recordExecution(plan.planId, "ord-00000001");
    expect(executed.status).toBe("executed");
    expect(executed.resultingOrderId).toBe("ord-00000001");
  });

  it("rejects empty rationale", () => {
    const store = createPlanStore();
    expect(() =>
      store.create({
        intent: "open_long",
        symbol: "BTC/USDT",
        orderParams: { side: "BUY", type: "MARKET", quantity: 0.01 },
        rationale: "   ", // 仅空白
      }),
    ).toThrow(PlanError);
  });

  it("rejects LIMIT without price / MARKET with price", () => {
    const store = createPlanStore();
    expect(() =>
      store.create({
        intent: "open_long",
        symbol: "BTC/USDT",
        orderParams: { side: "BUY", type: "LIMIT", quantity: 0.01 },
        rationale: "限价测试",
      }),
    ).toThrow(PlanError);
    expect(() =>
      store.create({
        intent: "open_long",
        symbol: "BTC/USDT",
        orderParams: {
          side: "BUY",
          type: "MARKET",
          quantity: 0.01,
          
          price: 50_000,
        },
        rationale: "市价测试",
      }),
    ).toThrow(PlanError);
  });

  it("approve fails on already-approved plan", () => {
    const store = createPlanStore();
    const plan = store.create({
      intent: "open_long",
      symbol: "BTC/USDT",
      orderParams: { side: "BUY", type: "MARKET", quantity: 0.01 },
      rationale: "重复审批测试",
    });
    store.approve({ planId: plan.planId, approver: "risk-agent" });
    expect(() => store.approve({ planId: plan.planId, approver: "risk-agent" })).toThrow(
      PlanError,
    );
  });

  it("token is single-use: second consume throws", () => {
    const store = createPlanStore();
    const plan = store.create({
      intent: "open_long",
      symbol: "BTC/USDT",
      orderParams: { side: "BUY", type: "MARKET", quantity: 0.01 },
      rationale: "token 一次性测试",
    });
    const approval = store.approve({ planId: plan.planId, approver: "risk-agent" });
    store.consumeApproval(plan.planId, approval.approvalToken);

    expect(() => store.consumeApproval(plan.planId, approval.approvalToken)).toThrow(
      PlanError,
    );
  });

  it("reject is terminal", () => {
    const store = createPlanStore();
    const plan = store.create({
      intent: "open_long",
      symbol: "BTC/USDT",
      orderParams: { side: "BUY", type: "MARKET", quantity: 0.01 },
      rationale: "拒绝测试",
    });
    store.reject({ planId: plan.planId, reason: "超出仓位", rejector: "risk-agent" });
    expect(store.get(plan.planId)!.status).toBe("rejected");
    expect(() => store.approve({ planId: plan.planId, approver: "risk-agent" })).toThrow(
      PlanError,
    );
  });

  it("expired plan cannot be approved", () => {
    const store = createPlanStore();
    const plan = store.create({
      intent: "open_long",
      symbol: "BTC/USDT",
      orderParams: { side: "BUY", type: "MARKET", quantity: 0.01 },
      rationale: "过期测试",
      expireInSeconds: 1,
    });
    // 手动把过期时间设到过去
    plan.expireAt = new Date(Date.now() - 1000);
    expect(() => store.approve({ planId: plan.planId, approver: "risk-agent" })).toThrow(
      PlanError,
    );
    expect(store.get(plan.planId)!.status).toBe("expired");
  });

  it("get unknown plan returns null", () => {
    const store = createPlanStore();
    expect(store.get("00000000-0000-0000-0000-000000000000")).toBeNull();
  });
});

// ────────────────────────────────────────────────────────────────────
// Tool layer — PlanError → { ok: false } 翻译
// ────────────────────────────────────────────────────────────────────

describe("createTradePlanTool", () => {
  it("returns ok: true with planId on happy path", async () => {
    const result = await createTradePlanTool.execute!(
      {
        intent: "open_long",
        venue: "binance",
        symbol: "BTC/USDT",
        side: "BUY",
        orderType: "MARKET",
        quantity: 0.01,
        
        rationale: "突破前高 + 量能配合",
        expireInSeconds: 300,
      } as never,
      ctx(),
    );
    expect((result as { ok: boolean }).ok).toBe(true);
    expect((result as { planId: string }).planId).toMatch(/^[0-9a-f-]{36}$/);
    expect((result as { status: string }).status).toBe("pending_approval");
  });

  it("returns ok: false with RATIONALE_REQUIRED when rationale empty", async () => {
    const result = await createTradePlanTool.execute!(
      {
        intent: "open_long",
        venue: "binance",
        symbol: "BTC/USDT",
        side: "BUY",
        orderType: "MARKET",
        quantity: 0.01,
        
        rationale: "  ",
        expireInSeconds: 300,
      } as never,
      ctx(),
    );
    expect((result as { ok: boolean }).ok).toBe(false);
    expect((result as { code: string }).code).toBe("RATIONALE_REQUIRED");
  });
});

describe("approveTradePlanTool", () => {
  it("approves a pending plan and returns token", async () => {
    const created = await createTradePlanTool.execute!(
      {
        intent: "open_long",
        venue: "binance",
        symbol: "BTC/USDT",
        side: "BUY",
        orderType: "MARKET",
        quantity: 0.01,
        
        rationale: "测试",
        expireInSeconds: 300,
      } as never,
      ctx(),
    );
    const planId = (created as { planId: string }).planId;

    const approval = await approveTradePlanTool.execute!(
      { planId, approver: "risk-agent" } as never,
      ctx(),
    );
    expect((approval as { ok: boolean }).ok).toBe(true);
    expect((approval as { approvalToken: string }).approvalToken).toMatch(
      /^[0-9a-f-]{36}$/,
    );
  });

  it("returns ok: false for unknown plan", async () => {
    const result = await approveTradePlanTool.execute!(
      { planId: "00000000-0000-0000-0000-000000000000", approver: "risk-agent" } as never,
      ctx(),
    );
    expect((result as { ok: boolean }).ok).toBe(false);
    expect((result as { code: string }).code).toBe("PLAN_NOT_FOUND");
  });
});

describe("rejectTradePlanTool", () => {
  it("rejects pending plan", async () => {
    const created = await createTradePlanTool.execute!(
      {
        intent: "open_long",
        venue: "binance",
        symbol: "BTC/USDT",
        side: "BUY",
        orderType: "MARKET",
        quantity: 0.01,
        
        rationale: "拒绝测试",
        expireInSeconds: 300,
      } as never,
      ctx(),
    );
    const planId = (created as { planId: string }).planId;

    const r = await rejectTradePlanTool.execute!(
      { planId, reason: "超仓位上限", rejector: "risk-agent" } as never,
      ctx(),
    );
    expect((r as { ok: boolean }).ok).toBe(true);
    expect((r as { status: string }).status).toBe("rejected");
  });
});

// ────────────────────────────────────────────────────────────────────
// executeTradePlanTool —— mock paper /orders/submit
// ────────────────────────────────────────────────────────────────────

describe("executeTradePlanTool", () => {
  it("happy path: consumes token + posts to paper + records order ID", async () => {
    let capturedBody = "";
    mockFetch(async (url, init) => {
      capturedBody = (init?.body as string) ?? "";
      expect(url).toContain("/orders/submit");
      return new Response(
        JSON.stringify({
          client_order_id: "ord-00000042",
          venue: "binance",
          symbol: "BTC/USDT",
          side: "BUY",
          order_type: "MARKET",
          requested_quantity: 0.01,
          requested_price: null,
          status: "FILLED",
          filled_quantity: 0.01,
          avg_fill_price: 50_000,
          fee: 0.5,
          notional: 500,
          rejection_reason: null,
          ts_event: "2026-05-21T00:00:00Z",
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    });

    const created = await createTradePlanTool.execute!(
      {
        intent: "open_long",
        venue: "binance",
        symbol: "BTC/USDT",
        side: "BUY",
        orderType: "MARKET",
        quantity: 0.01,
        
        rationale: "突破",
        expireInSeconds: 300,
      } as never,
      ctx(),
    );
    const planId = (created as { planId: string }).planId;

    const approval = await approveTradePlanTool.execute!(
      { planId, approver: "risk-agent" } as never,
      ctx(),
    );
    const token = (approval as { approvalToken: string }).approvalToken;

    const exec = await executeTradePlanTool.execute!(
      { planId, approvalToken: token } as never,
      ctx(),
    );

    expect((exec as { ok: boolean }).ok).toBe(true);
    expect((exec as { planStatus: string }).planStatus).toBe("executed");
    expect(
      (exec as { order: { clientOrderId: string } }).order.clientOrderId,
    ).toBe("ord-00000042");

    const body = JSON.parse(capturedBody);
    expect(body).toMatchObject({
      symbol: "BTC/USDT",
      side: "BUY",
      type: "MARKET",
      quantity: 0.01,
    });
    // D-8a' 后 refPrice 由 paper 服务端自取，不再由 client 携带
    expect(body.ref_price).toBeUndefined();

    // 二次 execute 必须失败（token 已消费）
    const exec2 = await executeTradePlanTool.execute!(
      { planId, approvalToken: token } as never,
      ctx(),
    );
    expect((exec2 as { ok: boolean }).ok).toBe(false);
  });

  it("rejects execution without approval", async () => {
    const created = await createTradePlanTool.execute!(
      {
        intent: "open_long",
        venue: "binance",
        symbol: "BTC/USDT",
        side: "BUY",
        orderType: "MARKET",
        quantity: 0.01,
        
        rationale: "无审批测试",
        expireInSeconds: 300,
      } as never,
      ctx(),
    );
    const planId = (created as { planId: string }).planId;

    const exec = await executeTradePlanTool.execute!(
      { planId, approvalToken: "00000000-0000-0000-0000-000000000000" } as never,
      ctx(),
    );
    expect((exec as { ok: boolean }).ok).toBe(false);
    expect((exec as { code: string }).code).toBe("INVALID_STATE");
  });
});

// ────────────────────────────────────────────────────────────────────
// getTradePlanTool
// ────────────────────────────────────────────────────────────────────

describe("getTradePlanTool", () => {
  it("returns plan dict for known ID", async () => {
    const created = await createTradePlanTool.execute!(
      {
        intent: "open_long",
        venue: "binance",
        symbol: "BTC/USDT",
        side: "BUY",
        orderType: "MARKET",
        quantity: 0.01,
        
        rationale: "查询测试",
        expireInSeconds: 300,
      } as never,
      ctx(),
    );
    const planId = (created as { planId: string }).planId;

    const got = await getTradePlanTool.execute!({ planId } as never, ctx());
    expect((got as { ok: boolean }).ok).toBe(true);
    expect((got as { plan: { planId: string } }).plan.planId).toBe(planId);
  });

  it("returns ok: false for unknown plan", async () => {
    const got = await getTradePlanTool.execute!(
      { planId: "00000000-0000-0000-0000-000000000000" } as never,
      ctx(),
    );
    expect((got as { ok: boolean }).ok).toBe(false);
  });
});
