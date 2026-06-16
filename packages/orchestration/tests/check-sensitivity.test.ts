/**
 * D-12 · paper.check_sensitivity tool 单测。
 *
 * 覆盖：POST /backtest/sensitivity 透传（含 token）、strategyId/candidateId 互斥、
 * 默认 permission allow。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { clearSettings, setSettings } from "../src/config.js";
import { DEFAULT_PERMISSIONS, PermissionEngine } from "../src/permissions/index.js";
import {
  paperCheckSensitivityTool,
  paperListBacktestTradesTool,
} from "../src/tools/index.js";

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

function mockFetch(impl: (url: string, init?: RequestInit) => Promise<Response>) {
  vi.stubGlobal("fetch", vi.fn(impl));
}

const ctx = (authToken: string | undefined = TEST_TOKEN): never =>
  ({ requestContext: { authToken } }) as never;

const SENSITIVITY_RESPONSE = {
  candidate_id: null,
  strategy_id: "sma_cross",
  base_fitness: 1.2,
  pct: 0.2,
  neighbors: [
    { params: { fast_period: 4 }, fitness: 1.0, error: null },
    { params: { fast_period: 6 }, fitness: 1.1, error: null },
  ],
  stats: { mean: 1.05, std: 0.07, worst: 1.0, n_ok: 2, n_failed: 0 },
  verdict: "robust",
};

describe("paper.check_sensitivity", () => {
  it("POSTs /backtest/sensitivity 透传参数与 token", async () => {
    let capturedUrl = "";
    let capturedAuth = "";
    let capturedBody: Record<string, unknown> = {};
    mockFetch(async (url, init) => {
      capturedUrl = url;
      capturedAuth = (init?.headers as Record<string, string>)?.Authorization ?? "";
      capturedBody = JSON.parse((init?.body as string) ?? "{}");
      return new Response(JSON.stringify(SENSITIVITY_RESPONSE), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    });

    const result = await paperCheckSensitivityTool.execute!(
      {
        strategyId: "sma_cross",
        params: { fast_period: 5, slow_period: 15, trade_size: 0.05 },
        symbol: "BTC/USDT",
        fromTs: "2026-01-01T00:00:00Z",
        toTs: "2026-01-06T00:00:00Z",
      } as never,
      ctx(),
    );

    expect(capturedUrl).toContain("/backtest/sensitivity");
    expect(capturedAuth).toBe(`Bearer ${TEST_TOKEN}`);
    expect(capturedBody.strategy_id).toBe("sma_cross");
    expect(capturedBody.params).toEqual({
      fast_period: 5,
      slow_period: 15,
      trade_size: 0.05,
    });
    expect(capturedBody.pct).toBe(0.2);
    expect((result as { verdict: string }).verdict).toBe("robust");
  });

  it("strategyId 与 candidateId 互斥（schema superRefine）", async () => {
    const schema = paperCheckSensitivityTool.inputSchema!;
    const both = schema.safeParse({
      strategyId: "sma_cross",
      candidateId: "550e8400-e29b-41d4-a716-446655440000",
      params: { p: 1 },
      symbol: "BTC/USDT",
      fromTs: "2026-01-01T00:00:00Z",
      toTs: "2026-01-06T00:00:00Z",
    });
    expect(both.success).toBe(false);

    const neither = schema.safeParse({
      params: { p: 1 },
      symbol: "BTC/USDT",
      fromTs: "2026-01-01T00:00:00Z",
      toTs: "2026-01-06T00:00:00Z",
    });
    expect(neither.success).toBe(false);
  });

  it("默认 permission = allow（只读回测扇出，无下单路径）", () => {
    const engine = new PermissionEngine(DEFAULT_PERMISSIONS);
    expect(engine.authorize("paper.check_sensitivity", {}).decision).toBe("allow");
  });
});

describe("paper.list_backtest_trades", () => {
  it("GET /backtest_runs/{runId}/trades 透传 limit + token", async () => {
    let capturedUrl = "";
    let capturedAuth = "";
    mockFetch(async (url, init) => {
      capturedUrl = url;
      capturedAuth = (init?.headers as Record<string, string>)?.Authorization ?? "";
      return new Response(
        JSON.stringify([
          {
            seq: 0,
            bar_ts: "2026-04-19T15:00:00Z",
            bar_close: 76024.01,
            side: "BUY",
            quantity: 0.007,
            order_type: "MARKET",
            fill_price: 76024.02,
            fee: 0.53,
            realized_pnl: 0,
            intent: "open_long",
            tag: null,
          },
        ]),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    });

    const result = await paperListBacktestTradesTool.execute!(
      { runId: "550e8400-e29b-41d4-a716-446655440000", limit: 5 } as never,
      ctx(),
    );

    expect(capturedUrl).toContain(
      "/backtest_runs/550e8400-e29b-41d4-a716-446655440000/trades",
    );
    expect(capturedUrl).toContain("limit=5");
    expect(capturedAuth).toBe(`Bearer ${TEST_TOKEN}`);
    expect((result as unknown[]).length).toBe(1);
    expect((result as { intent: string }[])[0].intent).toBe("open_long");
  });

  it("默认 permission = allow（命中 paper.list_* 通配）", () => {
    const engine = new PermissionEngine(DEFAULT_PERMISSIONS);
    expect(
      engine.authorize("paper.list_backtest_trades", {}).decision,
    ).toBe("allow");
  });
});
