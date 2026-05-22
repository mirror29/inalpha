/**
 * Tool 层单测 —— 用 vitest 的 fetch mock。
 *
 * Mastra 1.x 后 execute 签名是 ``(inputData, ctx)``，ctx.requestContext 替代旧的
 * runtimeContext。手动调 execute() 不走 inputSchema 校验。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { setSettings, clearSettings } from "../src/config.js";
import {
  dataBackfillBarsTool,
  dataGetBarsTool,
  paperListStrategiesTool,
  paperRunBacktestTool,
  researchDeepDiveTool,
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

/** 共享的 ctx fixture（绕过 Mastra 1.x 类型严格性，测试时不需要真实 ToolExecutionContext） */
const ctx = (authToken: string | undefined = TEST_TOKEN): never =>
  ({ requestContext: { authToken } }) as never;

// ────────────────────────────────────────────────────────────────────
// data.get_bars
// ────────────────────────────────────────────────────────────────────

describe("data.get_bars", () => {
  it("calls data-service /bars with correct params and forwards token", async () => {
    let capturedUrl = "";
    let capturedAuth = "";
    mockFetch(async (url, init) => {
      capturedUrl = url;
      capturedAuth = (init?.headers as Record<string, string>)?.Authorization ?? "";
      return new Response(
        JSON.stringify([
          {
            ts: "2026-01-01T00:00:00Z",
            venue: "binance",
            symbol: "BTC/USDT",
            timeframe: "1h",
            open: 100,
            high: 101,
            low: 99,
            close: 100.5,
            volume: 1.0,
          },
        ]),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    });

    const result = await dataGetBarsTool.execute!(
      {
        venue: "binance",
        symbol: "BTC/USDT",
        timeframe: "1h",
        fromTs: "2026-01-01T00:00:00Z",
        toTs: "2026-01-02T00:00:00Z",
        limit: 100,
      } as never,
      ctx(),
    );

    expect(capturedUrl).toContain("/bars");
    expect(capturedUrl).toContain("venue=binance");
    expect(capturedUrl).toContain("symbol=BTC%2FUSDT");
    expect(capturedUrl).toContain("timeframe=1h");
    expect(capturedAuth).toBe(`Bearer ${TEST_TOKEN}`);

    expect((result as { count: number }).count).toBe(1);
  });

  it("inputSchema rejects bad symbol format", () => {
    // Mastra createTool 在 LLM dispatch 时校验 inputSchema；手动调 execute 不走 schema。
    // 这里直接断言 schema 本身的拒绝行为，不通过 execute。
    const schema = dataGetBarsTool.inputSchema!;
    const result = schema.safeParse({
      venue: "binance",
      symbol: "not-a-valid-symbol",
      timeframe: "1h",
      fromTs: "2026-01-01T00:00:00Z",
      toTs: "2026-01-02T00:00:00Z",
      limit: 100,
    });
    expect(result.success).toBe(false);
  });

  it("falls back to service token when no authToken in requestContext", async () => {
    // dev 友好：缺 authToken 时自签 service token（不再抛错）
    let capturedAuth = "";
    mockFetch(async (_url, init) => {
      capturedAuth = (init?.headers as Record<string, string>)?.Authorization ?? "";
      return new Response("[]", { status: 200, headers: { "Content-Type": "application/json" } });
    });

    await dataGetBarsTool.execute!(
      {
        venue: "binance",
        symbol: "BTC/USDT",
        timeframe: "1h",
        fromTs: "2026-01-01T00:00:00Z",
        toTs: "2026-01-02T00:00:00Z",
        limit: 100,
      } as never,
      { requestContext: {} } as never,
    );

    expect(capturedAuth).toMatch(/^Bearer .+/); // 自签了 token
  });
});

// ────────────────────────────────────────────────────────────────────
// data.backfill_bars
// ────────────────────────────────────────────────────────────────────

describe("data.backfill_bars", () => {
  it("POSTs to /backfill/bars with body", async () => {
    let capturedBody = "";
    mockFetch(async (url, init) => {
      if (url.includes("/backfill/bars")) {
        capturedBody = (init?.body as string) ?? "";
        return new Response(
          JSON.stringify({
            venue: "binance",
            symbol: "BTC/USDT",
            timeframe: "1h",
            bars_fetched: 169,
            bars_inserted: 169,
            from_ts: "2026-05-14T00:00:00Z",
            to_ts: "2026-05-21T00:00:00Z",
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }
      return new Response("not found", { status: 404 });
    });

    const result = await dataBackfillBarsTool.execute!(
      {
        venue: "binance",
        symbol: "BTC/USDT",
        timeframe: "1h",
        fromTs: "2026-05-14T00:00:00Z",
        toTs: "2026-05-21T00:00:00Z",
      } as never,
      ctx(),
    );

    expect(JSON.parse(capturedBody)).toMatchObject({
      symbol: "BTC/USDT",
      from_ts: "2026-05-14T00:00:00Z",
    });
    expect((result as { bars_inserted: number }).bars_inserted).toBe(169);
  });
});

// ────────────────────────────────────────────────────────────────────
// paper.list_strategies
// ────────────────────────────────────────────────────────────────────

describe("paper.list_strategies", () => {
  it("returns strategy list", async () => {
    mockFetch(async () =>
      new Response(JSON.stringify({ strategies: ["sma_cross"] }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    const result = await paperListStrategiesTool.execute!({} as never, ctx());

    expect((result as { strategies: string[] }).strategies).toContain("sma_cross");
  });
});

// ────────────────────────────────────────────────────────────────────
// paper.run_backtest
// ────────────────────────────────────────────────────────────────────

describe("paper.run_backtest", () => {
  it("posts request and returns full report", async () => {
    let capturedBody = "";
    mockFetch(async (_url, init) => {
      capturedBody = (init?.body as string) ?? "";
      return new Response(
        JSON.stringify({
          strategy_id: "sma_cross",
          venue: "binance",
          symbol: "BTC/USDT",
          timeframe: "1h",
          initial_cash: 10000,
          final_equity: 9980.94,
          total_return_pct: -0.19,
          num_trades: 11,
          total_fees: 8.51,
          num_bars_processed: 169,
          period_start: "2026-05-14T00:00:00Z",
          period_end: "2026-05-21T00:00:00Z",
          final_positions: [],
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    });

    const result = await paperRunBacktestTool.execute!(
      {
        strategyId: "sma_cross",
        params: { fast_period: 5, slow_period: 20, trade_size: 0.01 },
        venue: "binance",
        symbol: "BTC/USDT",
        timeframe: "1h",
        fromTs: "2026-05-14T00:00:00Z",
        toTs: "2026-05-21T00:00:00Z",
        initialCash: 10000,
        feeRate: 0.001,
      } as never,
      ctx(),
    );

    expect(JSON.parse(capturedBody)).toMatchObject({
      strategy_id: "sma_cross",
      params: { fast_period: 5 },
    });
    expect((result as { num_trades: number }).num_trades).toBe(11);
  });

  it("translates upstream 400 to HttpClientError with original code", async () => {
    mockFetch(async () =>
      new Response(
        JSON.stringify({
          code: "NO_BARS_AVAILABLE",
          message: "no bars; backfill first",
          details: { venue: "binance" },
        }),
        { status: 400, headers: { "Content-Type": "application/json" } },
      ),
    );

    await expect(
      paperRunBacktestTool.execute!(
        {
          strategyId: "sma_cross",
          params: {},
          venue: "binance",
          symbol: "BTC/USDT",
          timeframe: "1h",
          fromTs: "2026-05-14T00:00:00Z",
          toTs: "2026-05-21T00:00:00Z",
          initialCash: 10000,
          feeRate: 0.001,
        } as never,
        ctx(),
      ),
    ).rejects.toMatchObject({ code: "NO_BARS_AVAILABLE", status: 400 });
  });
});


// ────────────────────────────────────────────────────────────────────
// research.deep_dive
// ────────────────────────────────────────────────────────────────────

describe("research.deep_dive", () => {
  it("posts to research-service /deep_dive and returns plan", async () => {
    let capturedUrl = "";
    let capturedBody = "";
    mockFetch(async (url, init) => {
      capturedUrl = url;
      capturedBody = (init?.body as string) ?? "";
      return new Response(
        JSON.stringify({
          venue: "binance",
          symbol: "BTC/USDT",
          timeframe: "1h",
          as_of: "2026-05-21T12:00:00Z",
          rating: "overweight",
          confidence: 0.7,
          thesis: "Clean upcross + neutral macro.",
          risks: ["fast RSI overbought"],
          suggested_action: "open_long 0.02",
          briefs: [],
          horizon: "swing",
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    });

    const result = (await researchDeepDiveTool.execute!(
      {
        venue: "binance",
        symbol: "BTC/USDT",
        timeframe: "1h",
        asOf: "2026-05-21T12:00:00Z",
        lookbackDays: 30,
        userQuestion: "should I buy BTC?",
      } as never,
      ctx(),
    )) as { rating: string; suggested_action: string };

    expect(capturedUrl).toContain("/deep_dive");
    expect(capturedUrl).toContain("research-mock.test");
    // 字段名 snake_case（与 services/research schemas.py 对齐）
    const body = JSON.parse(capturedBody);
    expect(body.as_of).toBe("2026-05-21T12:00:00Z");
    expect(body.lookback_days).toBe(30);
    expect(body.user_question).toBe("should I buy BTC?");

    expect(result.rating).toBe("overweight");
    expect(result.suggested_action).toBe("open_long 0.02");
  });

  it("rejects bad symbol via schema", () => {
    const r = researchDeepDiveTool.inputSchema!.safeParse({
      symbol: "BTCUSDT", // 缺斜杠
      timeframe: "1h",
      asOf: "2026-05-21T12:00:00Z",
    });
    expect(r.success).toBe(false);
  });

  it("rejects bad asOf via schema", () => {
    const r = researchDeepDiveTool.inputSchema!.safeParse({
      symbol: "BTC/USDT",
      timeframe: "1h",
      asOf: "not a datetime",
    });
    expect(r.success).toBe(false);
  });
});
