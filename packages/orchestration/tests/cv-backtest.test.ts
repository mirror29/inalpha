/**
 * ADR-0028 · paper.cv_backtest tool 单测。
 *
 * 覆盖：POST /backtest/cv 透传（splitter 参数 + token）、strategyId/candidateId 互斥、
 * cpcv nTestFolds < nFolds 校验、默认 permission allow。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { clearSettings, setSettings } from "../src/config.js";
import { DEFAULT_PERMISSIONS, PermissionEngine } from "../src/permissions/index.js";
import { paperCvBacktestTool } from "../src/tools/index.js";

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

const CV_RESPONSE = {
  symbol: "BTC/USDT",
  timeframe: "1d",
  n_bars: 300,
  splitter_used: "cpcv",
  n_paths: 5,
  n_splits: 30,
  sharpe_per_path: [0.2, 0.4, 0.5, 0.6, 0.9],
  max_dd_per_path: [10, 12, 8, 15, 7],
  sharpe_p5: 0.24,
  sharpe_p50: 0.5,
  sharpe_p95: 0.84,
  sharpe_mean: 0.52,
  dsr: 0.31,
  dsr_p_value: 0.12,
  note: null,
};

describe("paper.cv_backtest", () => {
  it("POSTs /backtest/cv 透传 splitter 参数与 token", async () => {
    let capturedUrl = "";
    let capturedAuth = "";
    let capturedBody: Record<string, unknown> = {};
    mockFetch(async (url, init) => {
      capturedUrl = url;
      capturedAuth = (init?.headers as Record<string, string>)?.Authorization ?? "";
      capturedBody = JSON.parse((init?.body as string) ?? "{}");
      return new Response(JSON.stringify(CV_RESPONSE), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    });

    const result = await paperCvBacktestTool.execute!(
      {
        strategyId: "sma_cross",
        params: { fast_period: 5, slow_period: 15 },
        symbol: "BTC/USDT",
        timeframe: "1d",
        fromTs: "2026-01-01T00:00:00Z",
        toTs: "2026-02-01T00:00:00Z",
        splitter: "cpcv",
        nFolds: 6,
        nTestFolds: 2,
      } as never,
      ctx(),
    );

    expect(capturedUrl).toContain("/backtest/cv");
    expect(capturedAuth).toBe(`Bearer ${TEST_TOKEN}`);
    expect(capturedBody.strategy_id).toBe("sma_cross");
    expect(capturedBody.splitter).toBe("cpcv");
    expect(capturedBody.n_folds).toBe(6);
    expect(capturedBody.n_test_folds).toBe(2);
    expect((result as { n_paths: number }).n_paths).toBe(5);
    expect((result as { sharpe_p50: number }).sharpe_p50).toBe(0.5);
  });

  it("strategyId 与 candidateId 互斥（schema superRefine）", () => {
    const schema = paperCvBacktestTool.inputSchema!;
    const both = schema.safeParse({
      strategyId: "sma_cross",
      candidateId: "550e8400-e29b-41d4-a716-446655440000",
      symbol: "BTC/USDT",
      fromTs: "2026-01-01T00:00:00Z",
      toTs: "2026-02-01T00:00:00Z",
    });
    expect(both.success).toBe(false);
  });

  it("cpcv 要求 nTestFolds < nFolds（schema superRefine）", () => {
    const schema = paperCvBacktestTool.inputSchema!;
    const bad = schema.safeParse({
      strategyId: "sma_cross",
      symbol: "BTC/USDT",
      fromTs: "2026-01-01T00:00:00Z",
      toTs: "2026-02-01T00:00:00Z",
      splitter: "cpcv",
      nFolds: 4,
      nTestFolds: 4,
    });
    expect(bad.success).toBe(false);
  });

  it("默认 permission = allow（只读回测扇出，无下单路径）", () => {
    const engine = new PermissionEngine(DEFAULT_PERMISSIONS);
    expect(engine.authorize("paper.cv_backtest", {}).decision).toBe("allow");
  });
});
