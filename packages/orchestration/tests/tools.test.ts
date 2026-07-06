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
  factorCatalogTool,
  factorPanelScoreTool,
  factorScoreTool,
  factorTimingTool,
  paperListStrategiesTool,
  paperListStrategyRunDecisionsTool,
  paperListStrategyRunsTool,
  paperRunBacktestTool,
  paperStartStrategyTool,
  paperStopStrategyTool,
  researchDeepDiveTool,
  researchParallelDiveTool,
} from "../src/tools/index.js";

const TEST_TOKEN = "test-token-doesnt-need-to-be-real";

beforeEach(() => {
  setSettings({
    dataServiceUrl: "http://data-mock.test",
    paperServiceUrl: "http://paper-mock.test",
    researchServiceUrl: "http://research-mock.test",
    factorServiceUrl: "http://factor-mock.test",
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
    // D-9 multi-market：放宽后接受 BTCUSDT / not-a-valid-symbol / BRK-B 等
    // （真实 ticker 含 hyphen 如 BRK-B），只拒空 / 空格 / 中文等真无效输入。
    const schema = dataGetBarsTool.inputSchema!;
    const result = schema.safeParse({
      venue: "binance",
      symbol: "bad symbol with space",
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

  /**
   * D-9 multi-venue 解锁回归：venue schema 从 z.literal("binance") 放宽到 z.string()，
   * 验证 yfinance / alpaca / akshare / fred 四种 venue 都能透传到后端 POST body，
   * 不再被 zod 卡掉 —— 这是用户问"特斯拉还能买吗"报错的根因修复测试。
   */
  it.each([
    { venue: "yfinance", symbol: "TSLA", timeframe: "1d" as const },
    { venue: "alpaca", symbol: "AAPL", timeframe: "1h" as const },
    { venue: "akshare", symbol: "sh.600519", timeframe: "1d" as const },
    { venue: "fred", symbol: "DFF", timeframe: "1d" as const },
  ])(
    "transparently forwards venue=$venue symbol=$symbol to /backfill/bars",
    async ({ venue, symbol, timeframe }) => {
      let capturedBody = "";
      mockFetch(async (url, init) => {
        if (url.includes("/backfill/bars")) {
          capturedBody = (init?.body as string) ?? "";
          return new Response(
            JSON.stringify({
              venue,
              symbol,
              timeframe,
              bars_fetched: 30,
              bars_inserted: 30,
              from_ts: "2026-04-25T00:00:00Z",
              to_ts: "2026-05-25T00:00:00Z",
            }),
            { status: 200, headers: { "Content-Type": "application/json" } },
          );
        }
        return new Response("not found", { status: 404 });
      });

      const result = await dataBackfillBarsTool.execute!(
        {
          venue,
          symbol,
          timeframe,
          fromTs: "2026-04-25T00:00:00Z",
          toTs: "2026-05-25T00:00:00Z",
        } as never,
        ctx(),
      );

      expect(JSON.parse(capturedBody)).toMatchObject({ venue, symbol, timeframe });
      expect((result as { venue: string; bars_inserted: number }).venue).toBe(venue);
      expect((result as { bars_inserted: number }).bars_inserted).toBe(30);
    },
  );
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

  it("downsamples giant equity_curve before it reaches LLM context", async () => {
    // 2026-06-11 事故回归：1 年 1h 曲线 ~8760 点原样进消息历史，几次回测
    // 就撑爆 DeepSeek 1M 上下文（AI_APICallError: maximum context length）。
    const bigCurve = Array.from({ length: 8760 }, (_, i) => ({
      ts: `t${i}`,
      equity: 10000 + i,
    }));
    mockFetch(async () =>
      new Response(
        JSON.stringify({
          strategy_id: "sma_cross",
          equity_curve: bigCurve,
          baseline: { strategy_id: "buy_and_hold", equity_curve: bigCurve },
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );

    const result = (await paperRunBacktestTool.execute!(
      {
        strategyId: "sma_cross",
        symbol: "BTC/USDT",
        venue: "binance",
        timeframe: "1h",
        initialCash: 10000,
        feeRate: 0.001,
        params: {},
      } as never,
      ctx(),
    )) as {
      equity_curve: { ts: string; equity: number }[];
      equity_curve_downsampled_from: number;
      baseline: { equity_curve: unknown[]; equity_curve_downsampled_from: number };
    };

    expect(result.equity_curve).toHaveLength(120);
    expect(result.equity_curve_downsampled_from).toBe(8760);
    // 首尾点必须保留（总收益形状不能漂）
    expect(result.equity_curve[0]!.ts).toBe("t0");
    expect(result.equity_curve[119]!.ts).toBe("t8759");
    // baseline 子报告同样降采样
    expect(result.baseline.equity_curve).toHaveLength(120);
    expect(result.baseline.equity_curve_downsampled_from).toBe(8760);
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
    // D-9 multi-market：BTCUSDT 不再被拒（plain ticker 合法）；改测真 invalid——含空格。
    const r = researchDeepDiveTool.inputSchema!.safeParse({
      symbol: "bad symbol with space",
      timeframe: "1h",
      asOf: "2026-05-21T12:00:00Z",
    });
    expect(r.success).toBe(false);
  });

  it("accepts multi-market symbol formats", () => {
    // D-9：4 类 venue 全支持
    const cases = ["BTC/USDT", "AAPL", "^N225", "sh.600519", "005930.KS", "DFF", "BRK-B"];
    for (const symbol of cases) {
      const r = researchDeepDiveTool.inputSchema!.safeParse({
        symbol,
        timeframe: "1d",
        asOf: "2026-05-21T12:00:00Z",
      });
      expect(r.success, `should accept ${symbol}`).toBe(true);
    }
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

// ────────────────────────────────────────────────────────────────────
// research.parallel_dive —— D-13 并行多视角扇出
// ────────────────────────────────────────────────────────────────────

describe("research.parallel_dive", () => {
  it("schema requires 2-4 perspectives", () => {
    const base = {
      symbol: "BTC/USDT",
      timeframe: "1h",
      asOf: "2026-05-21T12:00:00Z",
    };
    // 1 个视角 → 拒（min 2）
    expect(
      researchParallelDiveTool.inputSchema!.safeParse({
        ...base,
        perspectives: [{ lens: "bull", question: "请从多头视角分析这个标的的上涨理由" }],
      }).success,
    ).toBe(false);
    // 5 个视角 → 拒（max 4）
    expect(
      researchParallelDiveTool.inputSchema!.safeParse({
        ...base,
        perspectives: Array.from({ length: 5 }, (_, i) => ({
          lens: `p${i}`,
          question: "分析这个标的的某个维度",
        })),
      }).success,
    ).toBe(false);
    // 2 个视角 → 收
    expect(
      researchParallelDiveTool.inputSchema!.safeParse({
        ...base,
        perspectives: [
          { lens: "bull", question: "请从多头视角分析这个标的的上涨理由" },
          { lens: "bear", question: "请从空头视角分析这个标的的下跌风险" },
        ],
      }).success,
    ).toBe(true);
  });

  it("reuses research.ts SymbolSchema regex (rejects space)", () => {
    const r = researchParallelDiveTool.inputSchema!.safeParse({
      symbol: "bad symbol with space",
      timeframe: "1h",
      asOf: "2026-05-21T12:00:00Z",
      perspectives: [
        { lens: "bull", question: "请从多头视角分析这个标的的上涨理由" },
        { lens: "bear", question: "请从空头视角分析这个标的的下跌风险" },
      ],
    });
    expect(r.success).toBe(false);
  });

  it("aggregates succeeded/failed lanes when one lane fails", async () => {
    let callCount = 0;
    mockFetch(async () => {
      callCount += 1;
      // 第 2 个 lane 返 500 → 该 lane 落 failed，其余照常
      if (callCount === 2) {
        return new Response("boom", { status: 500 });
      }
      return new Response(
        JSON.stringify({
          research_id: `rid-${callCount}`,
          venue: "binance",
          symbol: "BTC/USDT",
          timeframe: "1h",
          as_of: "2026-05-21T12:00:00Z",
          rating: "overweight",
          confidence: 0.6,
          thesis: "thesis",
          risks: ["r1"],
          suggested_action: "hold",
          briefs: [],
          horizon: "swing",
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    });

    const result = (await researchParallelDiveTool.execute!(
      {
        venue: "binance",
        symbol: "BTC/USDT",
        timeframe: "1h",
        asOf: "2026-05-21T12:00:00Z",
        lookbackDays: 30,
        perspectives: [
          { lens: "bull", question: "请从多头视角分析这个标的的上涨理由" },
          { lens: "bear", question: "请从空头视角分析这个标的的下跌风险" },
        ],
      } as never,
      ctx(),
    )) as {
      total_lanes: number;
      succeeded: number;
      failed: number;
      lanes: { lens: string }[];
      errors: { lens: string }[];
    };

    expect(result.total_lanes).toBe(2);
    expect(result.succeeded).toBe(1);
    expect(result.failed).toBe(1);
    expect(result.lanes).toHaveLength(1);
    expect(result.errors).toHaveLength(1);
  });
});

// ────────────────────────────────────────────────────────────────────
// factor.* —— 接现成因子库（docs/miro/11）
// ────────────────────────────────────────────────────────────────────

describe("factor.timing / score / catalog", () => {
  it("factor.timing POSTs to factor-service /snapshot with snake_case body", async () => {
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
          as_of: "2026-06-01T00:00:00Z",
          horizon_bars: 5,
          bars_used: 800,
          available: true,
          reason: null,
          top_factors: [
            {
              factor_id: "pandas_ta.rsi_14",
              source: "pandas_ta",
              name: "RSI(14)",
              kind: "mean_reversion",
              value: 58.2,
              rank_ic: -0.04,
              icir: -0.8,
              sample_size: 700,
              quantile_returns: [],
              long_short_return: -0.01,
              direction: -1,
              strength: 0.8,
              low_confidence: false,
            },
          ],
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    });

    const result = (await factorTimingTool.execute!(
      {
        venue: "binance",
        symbol: "BTC/USDT",
        timeframe: "1h",
        lookbackBars: 720,
        horizonBars: 5,
        topN: 8,
      } as never,
      ctx(),
    )) as { available: boolean; top_factors: { factor_id: string; direction: number }[] };

    expect(capturedUrl).toContain("/snapshot");
    expect(capturedUrl).toContain("factor-mock.test");
    const body = JSON.parse(capturedBody);
    expect(body.lookback_bars).toBe(720);
    expect(body.horizon_bars).toBe(5);
    expect(body.top_n).toBe(8);
    expect(result.available).toBe(true);
    expect(result.top_factors[0].factor_id).toBe("pandas_ta.rsi_14");
    expect(result.top_factors[0].direction).toBe(-1);
  });

  it("factor.score POSTs to /score with factorIds → factor_ids", async () => {
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
          as_of: "2026-06-01T00:00:00Z",
          horizon_bars: 5,
          bars_used: 800,
          factors: [],
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    });

    await factorScoreTool.execute!(
      {
        venue: "binance",
        symbol: "BTC/USDT",
        timeframe: "1h",
        factorIds: ["pandas_ta.rsi_14", "qlib.kmid"],
      } as never,
      ctx(),
    );

    expect(capturedUrl).toContain("/score");
    expect(JSON.parse(capturedBody).factor_ids).toEqual(["pandas_ta.rsi_14", "qlib.kmid"]);
  });

  it("factor.panel_score POSTs to /panel/score with symbols + snake_case body", async () => {
    let capturedUrl = "";
    let capturedBody = "";
    mockFetch(async (url, init) => {
      capturedUrl = url;
      capturedBody = (init?.body as string) ?? "";
      return new Response(
        JSON.stringify({
          venue: "us",
          timeframe: "1d",
          as_of: "2026-06-01T00:00:00Z",
          horizon_bars: 5,
          symbols: ["AAPL", "MSFT", "GOOGL"],
          bars_used: { AAPL: 700, MSFT: 700, GOOGL: 700 },
          latest_bar_ts: {
            AAPL: "2026-06-24T00:00:00Z",
            MSFT: "2026-06-24T00:00:00Z",
            GOOGL: "2026-06-24T00:00:00Z",
          },
          is_pit: false,
          universe_note: "fixed non-PIT universe",
          factors: [
            {
              factor_id: "qlib.roc_20",
              source: "qlib_alpha158",
              name: "ROC(20)",
              kind: "momentum",
              ic_kind: "cross_sectional",
              cross_sectional_ic: 0.06,
              icir: 0.5,
              n_periods: 130,
              mean_valid_symbols: 3,
              low_confidence: false,
              latest_ranking_ts: "2026-06-24T00:00:00Z",
              latest_ranking: [
                { symbol: "GOOGL", value: -0.02, rank_pct: 0.33 },
                { symbol: "AAPL", value: 0.01, rank_pct: 0.67 },
                { symbol: "MSFT", value: 0.05, rank_pct: 1.0 },
              ],
            },
          ],
          ic_null_benchmark: 0.03,
          reason: null,
          unknown_factor_ids: [],
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    });

    const result = (await factorPanelScoreTool.execute!(
      {
        venue: "us",
        symbols: ["AAPL", "MSFT", "GOOGL"],
        timeframe: "1d",
        lookbackBars: 720,
        horizonBars: 5,
        minSymbols: 3,
      } as never,
      ctx(),
    )) as {
      is_pit: boolean;
      factors: { ic_kind: string; latest_ranking: { symbol: string }[] }[];
    };

    expect(capturedUrl).toContain("/panel/score");
    expect(capturedUrl).toContain("factor-mock.test");
    const body = JSON.parse(capturedBody);
    expect(body.symbols).toEqual(["AAPL", "MSFT", "GOOGL"]);
    expect(body.min_symbols).toBe(3);
    expect(body.lookback_bars).toBe(720);
    expect(result.is_pit).toBe(false);
    expect(result.factors[0].ic_kind).toBe("cross_sectional");
    expect(result.factors[0].latest_ranking[0].symbol).toBe("GOOGL");
  });

  it("factor.panel_score rejects <2 symbols via schema", () => {
    const r = factorPanelScoreTool.inputSchema!.safeParse({
      venue: "us",
      symbols: ["AAPL"],
      timeframe: "1d",
    });
    expect(r.success).toBe(false);
  });

  it("factor.catalog GETs /catalog", async () => {
    let capturedUrl = "";
    mockFetch(async (url) => {
      capturedUrl = url;
      return new Response(
        JSON.stringify({ factors: [], sources: { pandas_ta: true, qlib_alpha158: false } }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    });

    const result = (await factorCatalogTool.execute!({} as never, ctx())) as {
      sources: Record<string, boolean>;
    };
    expect(capturedUrl).toContain("/catalog");
    expect(result.sources.pandas_ta).toBe(true);
  });

  it("rejects bad symbol via schema", () => {
    const r = factorTimingTool.inputSchema!.safeParse({ symbol: "bad symbol", timeframe: "1h" });
    expect(r.success).toBe(false);
  });
});

// ────────────────────────────────────────────────────────────────────
// D-11 · live runner tools
// ────────────────────────────────────────────────────────────────────

describe("paper.start_strategy / stop / list", () => {
  it("start_strategy POSTs to /strategy_runs with candidate_id + market", async () => {
    let capturedUrl = "";
    let capturedBody = "";
    mockFetch(async (url, init) => {
      capturedUrl = url;
      capturedBody = (init?.body as string) ?? "";
      return new Response(
        JSON.stringify({
          id: "run-1", candidate_id: "cand-1", account_id: "acc-1", status: "running",
          venue: "binance", symbol: "BTC/USDT", timeframe: "1h", params: {},
          last_bar_ts: null, cumulative_pnl: 0, error_log: [],
          started_at: "2026-06-02T00:00:00Z", stopped_at: null,
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    });

    const result = await paperStartStrategyTool.execute!(
      {
        candidateId: "550e8400-e29b-41d4-a716-446655440000",
        venue: "binance", symbol: "BTC/USDT", timeframe: "1h",
      } as never,
      ctx(),
    );

    expect(capturedUrl).toContain("/strategy_runs");
    expect(JSON.parse(capturedBody)).toMatchObject({
      candidate_id: "550e8400-e29b-41d4-a716-446655440000",
      venue: "binance",
      symbol: "BTC/USDT",
      timeframe: "1h",
    });
    expect((result as { status: string }).status).toBe("running");
  });

  it("stop_strategy POSTs to /strategy_runs/{id}/stop", async () => {
    let capturedUrl = "";
    mockFetch(async (url) => {
      capturedUrl = url;
      return new Response(
        JSON.stringify({
          id: "run-1", candidate_id: "c", account_id: "a", status: "stopped",
          venue: "binance", symbol: "BTC/USDT", timeframe: "1h", params: {},
          last_bar_ts: null, cumulative_pnl: 0, error_log: [],
          started_at: "2026-06-02T00:00:00Z", stopped_at: "2026-06-02T01:00:00Z",
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    });

    const runId = "550e8400-e29b-41d4-a716-446655440001";
    const result = await paperStopStrategyTool.execute!({ runId } as never, ctx());
    expect(capturedUrl).toContain(`/strategy_runs/${runId}/stop`);
    expect((result as { status: string }).status).toBe("stopped");
  });

  it("list_strategy_runs GETs /strategy_runs", async () => {
    let capturedUrl = "";
    mockFetch(async (url) => {
      capturedUrl = url;
      return new Response(JSON.stringify([]), {
        status: 200, headers: { "Content-Type": "application/json" },
      });
    });

    await paperListStrategyRunsTool.execute!({ status: "running" } as never, ctx());
    expect(capturedUrl).toContain("/strategy_runs");
    expect(capturedUrl).toContain("status=running");
  });

  it("list_strategy_run_decisions GETs /strategy_runs/{id}/decisions", async () => {
    let capturedUrl = "";
    mockFetch(async (url) => {
      capturedUrl = url;
      return new Response(JSON.stringify([]), {
        status: 200, headers: { "Content-Type": "application/json" },
      });
    });
    const runId = "550e8400-e29b-41d4-a716-446655440002";
    await paperListStrategyRunDecisionsTool.execute!({ runId, limit: 50 } as never, ctx());
    expect(capturedUrl).toContain(`/strategy_runs/${runId}/decisions`);
    expect(capturedUrl).toContain("limit=50");
  });

  // D-11.1：起跑信任边界 / 资源护栏的错误码原样透传给 agent（issue #36.1 / #36.2）
  it("start_strategy 透传 403 CANDIDATE_NOT_OWNED（挂别人的 candidate）", async () => {
    mockFetch(async () =>
      new Response(
        JSON.stringify({
          code: "CANDIDATE_NOT_OWNED",
          message: "candidate is owned by another account",
          details: { candidate_id: "550e8400-e29b-41d4-a716-446655440000" },
        }),
        { status: 403, headers: { "Content-Type": "application/json" } },
      ),
    );

    await expect(
      paperStartStrategyTool.execute!(
        {
          candidateId: "550e8400-e29b-41d4-a716-446655440000",
          venue: "binance", symbol: "BTC/USDT", timeframe: "1h",
        } as never,
        ctx(),
      ),
    ).rejects.toMatchObject({ code: "CANDIDATE_NOT_OWNED", status: 403 });
  });

  it("start_strategy 透传 429 TOO_MANY_RUNNING_RUNS（超 per-account 上限）", async () => {
    mockFetch(async () =>
      new Response(
        JSON.stringify({
          code: "TOO_MANY_RUNNING_RUNS",
          message: "account already has 10 running strategy_runs (limit 10)",
          details: { running: 10, limit: 10 },
        }),
        { status: 429, headers: { "Content-Type": "application/json" } },
      ),
    );

    await expect(
      paperStartStrategyTool.execute!(
        {
          candidateId: "550e8400-e29b-41d4-a716-446655440003",
          venue: "binance", symbol: "ETH/USDT", timeframe: "1h",
        } as never,
        ctx(),
      ),
    ).rejects.toMatchObject({ code: "TOO_MANY_RUNNING_RUNS", status: 429 });
  });
});
