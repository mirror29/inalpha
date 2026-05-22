/**
 * D-8b trade-plan tool 单测 —— 用 mock fetch 模拟 paper service 响应。
 *
 * 范围：
 * - tool 把 paper HTTP 响应正确翻成 ``{ ok: true, ... }`` 格式
 * - 4xx 业务错误（HttpClientError code in PLAN_NOT_FOUND, INVALID_STATE, ...）
 *   翻成 ``{ ok: false, code, message }``
 * - 5xx / 网络错误抛 HttpClientError（不吞）
 * - JWT 透传到 paper 请求头
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { clearSettings, setSettings } from "../src/config.js";
import {
  approveTradePlanTool,
  createTradePlanTool,
  executeTradePlanTool,
  getTradePlanTool,
  rejectTradePlanTool,
} from "../src/tools/trade-plan.js";

const TEST_TOKEN = "test-token-doesnt-need-to-be-real";

beforeEach(() => {
  setSettings({
    dataServiceUrl: "http://data-mock.test",
    paperServiceUrl: "http://paper-mock.test",
    researchServiceUrl: "http://research-mock.test",
    jwtSecret: "test-secret-32-chars-or-more-xxxxxxx",
    jwtAlgorithm: "HS256",
  });
});

afterEach(() => {
  clearSettings();
  vi.restoreAllMocks();
});

const ctx = (authToken: string | undefined = TEST_TOKEN): never =>
  ({ requestContext: { authToken } }) as never;

function mockFetch(impl: (url: string, init?: RequestInit) => Promise<Response>) {
  vi.stubGlobal("fetch", vi.fn(impl));
}

function mockPaperResponse(
  matcher: (url: string, init?: RequestInit) => Response | null,
): { capturedRequests: { url: string; body: string | null; auth: string | null }[] } {
  const captured: { url: string; body: string | null; auth: string | null }[] = [];
  mockFetch(async (url, init) => {
    captured.push({
      url,
      body: (init?.body as string | undefined) ?? null,
      auth: (init?.headers as Record<string, string> | undefined)?.Authorization ?? null,
    });
    const r = matcher(url, init);
    if (r) return r;
    return new Response('{"code":"ROUTE_NOT_MOCKED","message":"' + url + '"}', {
      status: 500,
      headers: { "Content-Type": "application/json" },
    });
  });
  return { capturedRequests: captured };
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const PLAN_FIXTURE = {
  plan_id: "5bb728f2-8214-418f-9b75-3fa9e96986d7",
  account_id: "00000000-0000-0000-0000-000000000001",
  intent: "open_long",
  venue: "binance",
  symbol: "BTC/USDT",
  order_params: { side: "BUY", type: "MARKET", quantity: 0.0001 },
  risk_params: {},
  rationale: "smoke",
  status: "pending_approval",
  approval_token: null,
  approved_by: null,
  rejection_reason: null,
  created_at: "2026-05-22T10:00:00Z",
  approved_at: null,
  executed_at: null,
  expire_at: "2026-05-22T10:05:00Z",
  resulting_order_id: null,
};

// ────────────────────────────────────────────────────────────────────
// createTradePlanTool
// ────────────────────────────────────────────────────────────────────

describe("createTradePlanTool", () => {
  it("POSTs /plans with JWT and returns planId", async () => {
    const { capturedRequests } = mockPaperResponse((url) =>
      url.endsWith("/plans") ? jsonResponse(PLAN_FIXTURE) : null,
    );

    const result = await createTradePlanTool.execute!(
      {
        intent: "open_long",
        venue: "binance",
        symbol: "BTC/USDT",
        side: "BUY",
        orderType: "MARKET",
        quantity: 0.0001,
        rationale: "突破前高",
        expireInSeconds: 300,
      } as never,
      ctx(),
    );

    expect((result as { ok: boolean }).ok).toBe(true);
    expect((result as { planId: string }).planId).toBe(PLAN_FIXTURE.plan_id);
    expect((result as { status: string }).status).toBe("pending_approval");

    expect(capturedRequests.length).toBe(1);
    expect(capturedRequests[0].auth).toBe(`Bearer ${TEST_TOKEN}`);
    const body = JSON.parse(capturedRequests[0].body!);
    expect(body).toMatchObject({
      intent: "open_long",
      symbol: "BTC/USDT",
      side: "BUY",
      type: "MARKET",
      quantity: 0.0001,
      rationale: "突破前高",
    });
    // refPrice **不应该**在请求体里 —— 服务端自取
    expect(body.ref_price).toBeUndefined();
    expect(body.refPrice).toBeUndefined();
  });

  it("translates 400 RATIONALE_REQUIRED to ok:false result", async () => {
    mockPaperResponse((url) =>
      url.endsWith("/plans")
        ? jsonResponse({ code: "RATIONALE_REQUIRED", message: "empty rationale" }, 400)
        : null,
    );

    const result = await createTradePlanTool.execute!(
      {
        intent: "open_long",
        venue: "binance",
        symbol: "BTC/USDT",
        side: "BUY",
        orderType: "MARKET",
        quantity: 0.0001,
        rationale: "  ",
        expireInSeconds: 300,
      } as never,
      ctx(),
    );

    expect((result as { ok: boolean }).ok).toBe(false);
    expect((result as { code: string }).code).toBe("RATIONALE_REQUIRED");
  });

  it("propagates 5xx as HttpClientError (does not swallow)", async () => {
    mockPaperResponse((url) =>
      url.endsWith("/plans") ? jsonResponse({ message: "boom" }, 503) : null,
    );

    await expect(
      createTradePlanTool.execute!(
        {
          intent: "open_long",
          venue: "binance",
          symbol: "BTC/USDT",
          side: "BUY",
          orderType: "MARKET",
          quantity: 0.0001,
          rationale: "test",
          expireInSeconds: 300,
        } as never,
        ctx(),
      ),
    ).rejects.toMatchObject({ status: 503 });
  });
});

// ────────────────────────────────────────────────────────────────────
// approveTradePlanTool / rejectTradePlanTool
// ────────────────────────────────────────────────────────────────────

describe("approveTradePlanTool", () => {
  it("POSTs /plans/{id}/approve and returns token", async () => {
    const approvedPlan = {
      ...PLAN_FIXTURE,
      status: "approved",
      approval_token: "tok-abc-123",
      approved_by: "orchestrator",
      approved_at: "2026-05-22T10:01:00Z",
    };
    const { capturedRequests } = mockPaperResponse((url) =>
      url.endsWith(`/plans/${PLAN_FIXTURE.plan_id}/approve`)
        ? jsonResponse(approvedPlan)
        : null,
    );

    const r = await approveTradePlanTool.execute!(
      { planId: PLAN_FIXTURE.plan_id, approver: "orchestrator" } as never,
      ctx(),
    );

    expect((r as { ok: boolean }).ok).toBe(true);
    expect((r as { approvalToken: string }).approvalToken).toBe("tok-abc-123");
    const body = JSON.parse(capturedRequests[0].body!);
    expect(body).toEqual({ approver: "orchestrator" });
  });

  it("translates 400 PLAN_NOT_FOUND to ok:false", async () => {
    mockPaperResponse(() =>
      jsonResponse({ code: "PLAN_NOT_FOUND", message: "no such plan" }, 400),
    );

    const r = await approveTradePlanTool.execute!(
      { planId: "00000000-0000-0000-0000-000000000000", approver: "x" } as never,
      ctx(),
    );

    expect((r as { ok: boolean }).ok).toBe(false);
    expect((r as { code: string }).code).toBe("PLAN_NOT_FOUND");
  });
});

describe("rejectTradePlanTool", () => {
  it("POSTs /plans/{id}/reject with reason", async () => {
    const rejectedPlan = {
      ...PLAN_FIXTURE,
      status: "rejected",
      rejection_reason: "超出仓位",
    };
    const { capturedRequests } = mockPaperResponse((url) =>
      url.endsWith(`/plans/${PLAN_FIXTURE.plan_id}/reject`)
        ? jsonResponse(rejectedPlan)
        : null,
    );

    const r = await rejectTradePlanTool.execute!(
      {
        planId: PLAN_FIXTURE.plan_id,
        reason: "超出仓位",
        rejector: "risk-agent",
      } as never,
      ctx(),
    );

    expect((r as { ok: boolean }).ok).toBe(true);
    expect((r as { status: string }).status).toBe("rejected");
    const body = JSON.parse(capturedRequests[0].body!);
    expect(body).toEqual({ reason: "超出仓位", rejector: "risk-agent" });
  });
});

// ────────────────────────────────────────────────────────────────────
// executeTradePlanTool
// ────────────────────────────────────────────────────────────────────

describe("executeTradePlanTool", () => {
  it("POSTs /plans/{id}/execute and translates order result", async () => {
    const executeResponse = {
      plan_id: PLAN_FIXTURE.plan_id,
      plan_status: "executed",
      order: {
        client_order_id: "ord-00000001",
        venue: "binance",
        symbol: "BTC/USDT",
        side: "BUY",
        order_type: "MARKET",
        requested_quantity: 0.0001,
        requested_price: null,
        status: "FILLED",
        filled_quantity: 0.0001,
        avg_fill_price: 77000,
        fee: 0.0077,
        notional: 7.7,
        rejection_reason: null,
        ts_event: "2026-05-22T10:02:00Z",
      },
    };

    const { capturedRequests } = mockPaperResponse((url) =>
      url.endsWith(`/plans/${PLAN_FIXTURE.plan_id}/execute`)
        ? jsonResponse(executeResponse)
        : null,
    );

    const r = await executeTradePlanTool.execute!(
      {
        planId: PLAN_FIXTURE.plan_id,
        approvalToken: "tok-abc-123",
      } as never,
      ctx(),
    );

    expect((r as { ok: boolean }).ok).toBe(true);
    expect((r as { planStatus: string }).planStatus).toBe("executed");
    expect((r as { order: { clientOrderId: string } }).order.clientOrderId).toBe(
      "ord-00000001",
    );
    expect((r as { order: { avgFillPrice: number } }).order.avgFillPrice).toBe(77000);

    const body = JSON.parse(capturedRequests[0].body!);
    expect(body).toEqual({ approvalToken: "tok-abc-123" });
  });

  it("translates 400 INVALID_STATE to ok:false (e.g. token already consumed)", async () => {
    mockPaperResponse(() =>
      jsonResponse(
        {
          code: "INVALID_STATE",
          message: "plan not in approved state",
          details: { planId: PLAN_FIXTURE.plan_id, status: "executed" },
        },
        400,
      ),
    );

    const r = await executeTradePlanTool.execute!(
      { planId: PLAN_FIXTURE.plan_id, approvalToken: "stale-token" } as never,
      ctx(),
    );

    expect((r as { ok: boolean }).ok).toBe(false);
    expect((r as { code: string }).code).toBe("INVALID_STATE");
  });
});

// ────────────────────────────────────────────────────────────────────
// getTradePlanTool
// ────────────────────────────────────────────────────────────────────

describe("getTradePlanTool", () => {
  it("GETs /plans/{id} and returns plan snapshot", async () => {
    mockPaperResponse((url) =>
      url.endsWith(`/plans/${PLAN_FIXTURE.plan_id}`) ? jsonResponse(PLAN_FIXTURE) : null,
    );

    const r = await getTradePlanTool.execute!(
      { planId: PLAN_FIXTURE.plan_id } as never,
      ctx(),
    );

    expect((r as { ok: boolean }).ok).toBe(true);
    expect((r as { plan: { planId: string } }).plan.planId).toBe(PLAN_FIXTURE.plan_id);
    expect((r as { plan: { status: string } }).plan.status).toBe("pending_approval");
  });

  it("translates 400 PLAN_NOT_FOUND to ok:false", async () => {
    mockPaperResponse(() =>
      jsonResponse({ code: "PLAN_NOT_FOUND", message: "no such plan" }, 400),
    );

    const r = await getTradePlanTool.execute!(
      { planId: "00000000-0000-0000-0000-000000000000" } as never,
      ctx(),
    );

    expect((r as { ok: boolean }).ok).toBe(false);
  });
});
