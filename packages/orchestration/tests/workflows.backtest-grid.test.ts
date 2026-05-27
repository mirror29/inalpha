/**
 * backtest_grid workflow 验证（ADR-0025 §D3）。
 *
 * 三件事要测：
 *
 * 1. **expand 纯函数**：笛卡尔积 + dedupe（同 strategy+symbol+window）
 * 2. **aggregate 纯函数**：Pareto 前沿（Sharpe vs maxDD）+ topK by Sharpe
 * 3. **端到端 via mastra.getWorkflow**：mock paper /backtest，验证 9 job grid 并发执行，
 *    error 隔离，summary 字段正确
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { clearSettings, setSettings } from "../src/config.js";
import { mastra } from "../src/mastra/index.js";
import {
  computeParetoFrontier,
  GridInputSchema,
  pickTopK,
} from "../src/mastra/workflows/backtest-grid.js";

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

// ────────────────────────────────────────────────────────────────────
// Pure: computeParetoFrontier
// ────────────────────────────────────────────────────────────────────

describe("computeParetoFrontier", () => {
  it("returns single point as its own frontier", () => {
    const points = [
      { strategy_id: "a", symbol: "BTC/USDT", sharpe: 1.5, max_drawdown_pct: 10, total_return_pct: 20 },
    ];
    expect(computeParetoFrontier(points)).toEqual(points);
  });

  it("dominated points are removed (higher sharpe + lower DD dominates)", () => {
    const a = { strategy_id: "a", symbol: "BTC/USDT", sharpe: 1.5, max_drawdown_pct: 10, total_return_pct: 20 };
    const b = { strategy_id: "b", symbol: "BTC/USDT", sharpe: 1.0, max_drawdown_pct: 15, total_return_pct: 10 };
    // b 被 a 严格 dominate（sharpe a > b AND maxDD a < b）
    const out = computeParetoFrontier([a, b]);
    expect(out).toEqual([a]);
  });

  it("non-dominated points all stay in frontier", () => {
    // 两个点互不 dominate：A sharpe 高但 DD 也高；B 反之
    const a = { strategy_id: "a", symbol: "BTC/USDT", sharpe: 2.0, max_drawdown_pct: 20, total_return_pct: 30 };
    const b = { strategy_id: "b", symbol: "BTC/USDT", sharpe: 1.0, max_drawdown_pct: 5, total_return_pct: 8 };
    const out = computeParetoFrontier([a, b]);
    expect(out).toHaveLength(2);
    expect(out).toContain(a);
    expect(out).toContain(b);
  });

  it("null sharpe is excluded from frontier", () => {
    const a = { strategy_id: "a", symbol: "BTC/USDT", sharpe: null, max_drawdown_pct: 5, total_return_pct: 10 };
    const b = { strategy_id: "b", symbol: "BTC/USDT", sharpe: 1.0, max_drawdown_pct: 15, total_return_pct: 12 };
    const out = computeParetoFrontier([a, b]);
    expect(out).toEqual([b]);
  });
});

describe("pickTopK", () => {
  it("returns top K by sharpe (desc), nulls sorted last", () => {
    const pts = [
      { strategy_id: "a", symbol: "BTC/USDT", sharpe: 1.0, max_drawdown_pct: 5, total_return_pct: 10 },
      { strategy_id: "b", symbol: "ETH/USDT", sharpe: 2.0, max_drawdown_pct: 8, total_return_pct: 22 },
      { strategy_id: "c", symbol: "SOL/USDT", sharpe: null, max_drawdown_pct: 12, total_return_pct: 5 },
      { strategy_id: "d", symbol: "BNB/USDT", sharpe: 1.5, max_drawdown_pct: 7, total_return_pct: 15 },
    ];
    const top3 = pickTopK(pts, 3);
    expect(top3.map((p) => p.strategy_id)).toEqual(["b", "d", "a"]);
  });
});

// ────────────────────────────────────────────────────────────────────
// End-to-end via mastra.getWorkflow + fetch mock
// ────────────────────────────────────────────────────────────────────

type FetchCall = { url: string; body: unknown };

type BacktestRequestBody = {
  strategy_id?: string | null;
  candidate_id?: string | null;
  symbol: string;
};

function mockPaperBacktest(opts: {
  failPredicate?: (body: BacktestRequestBody) => boolean;
  reportFactory?: (body: BacktestRequestBody) => Record<string, unknown>;
}): FetchCall[] {
  const calls: FetchCall[] = [];
  const defaultReport = (body: BacktestRequestBody) => {
    const isCandidate = !!body.candidate_id;
    // candidate 路径下 strategy_id = 'candidate:<uuid>' 字面（仿真实 paper service 响应）
    const reportStrategyId = isCandidate
      ? `candidate:${body.candidate_id}`
      : body.strategy_id ?? "unknown";
    return {
      run_id: null,
      research_id: null,
      params_hash: "deadbeef",
      strategy_id: reportStrategyId,
      candidate_id: body.candidate_id ?? null,
      venue: "binance",
      symbol: body.symbol,
      timeframe: "1h",
      initial_cash: 10_000,
      final_equity: 10_500,
      total_return_pct: 5,
      num_trades: 3,
      total_fees: 1.2,
      num_bars_processed: 100,
      period_start: "2024-01-01T00:00:00Z",
      period_end: "2024-12-31T00:00:00Z",
      sharpe: 1.5,
      sortino: 1.8,
      max_drawdown_pct: 8,
      win_rate: 0.55,
      final_positions: [],
      // D-9 新字段
      fitness: isCandidate ? 1.2 : null,
      baseline: isCandidate
        ? {
            strategy_id: "buy_and_hold",
            fitness: 0.5,
            sharpe: 0.8,
            max_drawdown_pct: 12,
            total_return_pct: 4,
            num_trades: 1,
          }
        : null,
    };
  };

  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const body = JSON.parse(String(init?.body ?? "{}"));
      calls.push({ url, body });
      if (opts.failPredicate?.(body)) {
        return new Response(JSON.stringify({ code: "BACKTEST_FAIL", message: "engine crashed" }), {
          status: 500,
          headers: { "content-type": "application/json" },
        });
      }
      const report = opts.reportFactory?.(body) ?? defaultReport(body);
      return new Response(JSON.stringify(report), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    }),
  );
  return calls;
}

describe("backtest_grid workflow (end-to-end)", () => {
  it("expands strategies × symbols into N jobs and aggregates Pareto", async () => {
    const calls = mockPaperBacktest({
      reportFactory: (body) => {
        // 让不同 strategy+symbol 组合产生不同 sharpe / max_dd 以验 Pareto
        const sharpeMap: Record<string, number> = {
          "sma_cross|BTC/USDT": 2.0,
          "sma_cross|ETH/USDT": 1.5,
          "buy_and_hold|BTC/USDT": 1.0,
          "buy_and_hold|ETH/USDT": 0.8,
        };
        const ddMap: Record<string, number> = {
          "sma_cross|BTC/USDT": 12,
          "sma_cross|ETH/USDT": 10,
          "buy_and_hold|BTC/USDT": 25,
          "buy_and_hold|ETH/USDT": 30,
        };
        const k = `${body.strategy_id}|${body.symbol}`;
        return {
          run_id: null, research_id: null, params_hash: "x",
          strategy_id: body.strategy_id, venue: "binance", symbol: body.symbol, timeframe: "1h",
          initial_cash: 10_000, final_equity: 12_000, total_return_pct: 20,
          num_trades: 5, total_fees: 1.0, num_bars_processed: 100,
          period_start: "2024-01-01T00:00:00Z", period_end: "2024-12-31T00:00:00Z",
          sharpe: sharpeMap[k] ?? 1.0,
          sortino: 1.5,
          max_drawdown_pct: ddMap[k] ?? 15,
          win_rate: 0.5,
          final_positions: [],
        };
      },
    });

    const wf = mastra.getWorkflow("backtest_grid");
    const run = await wf.createRun();
    const result = await run.start({
      inputData: {
        strategies: ["sma_cross", "buy_and_hold"],
        symbols: ["BTC/USDT", "ETH/USDT"],
        venue: "binance",
        timeframe: "1h",
        from_ts: "2024-01-01T00:00:00Z",
        to_ts: "2024-12-31T00:00:00Z",
        initial_cash: 10_000,
        fee_rate: 0.001,
      },
    });

    expect(result.status).toBe("success");
    if (result.status !== "success") return;

    // 4 个 job 都成功
    expect(result.result.reports).toHaveLength(4);
    expect(result.result.summary.ok).toBe(4);
    expect(result.result.summary.errored).toBe(0);
    expect(calls).toHaveLength(4);

    // Pareto：sma_cross/BTC（高 sharpe）跟 sma_cross/ETH（低 DD 配 mid sharpe）应该都在前沿
    // buy_and_hold 两个被 dominate
    const paretoKeys = result.result.pareto.map((p) => `${p.strategy_id}|${p.symbol}`).sort();
    expect(paretoKeys).toEqual(["sma_cross|BTC/USDT", "sma_cross|ETH/USDT"]);

    // top_k：sharpe 2.0 > 1.5 > 1.0 > 0.8
    expect(result.result.top_k[0].sharpe).toBe(2.0);
    expect(result.result.top_k).toHaveLength(3);
  });

  it("dedupes duplicate (strategy, symbol) pairs in input", async () => {
    const calls = mockPaperBacktest({});

    const wf = mastra.getWorkflow("backtest_grid");
    const run = await wf.createRun();
    // strategies 列表里没法直接传重复（enum 又不强制 unique），靠 expand 内部去重
    // ➜ 这里用同 strategy+symbol 不同时间窗口 验证不去重 / 同窗口去重
    // 但 input schema 单 window，重复 strategy 列表算 dedupe
    const result = await run.start({
      inputData: {
        strategies: ["sma_cross", "sma_cross"], // 故意重复
        symbols: ["BTC/USDT"],
        venue: "binance",
        timeframe: "1h",
        from_ts: "2024-01-01T00:00:00Z",
        to_ts: "2024-12-31T00:00:00Z",
        initial_cash: 10_000,
        fee_rate: 0.001,
      },
    });

    expect(result.status).toBe("success");
    if (result.status !== "success") return;
    // 仅 1 个 job 真正打出去（dedup 后）
    expect(calls).toHaveLength(1);
    expect(result.result.reports).toHaveLength(1);
  });

  it("isolates per-job failures (one fails, others succeed)", async () => {
    const calls = mockPaperBacktest({
      // sma_cross + ETH/USDT 这一组炸
      failPredicate: (body) => body.strategy_id === "sma_cross" && body.symbol === "ETH/USDT",
    });

    const wf = mastra.getWorkflow("backtest_grid");
    const run = await wf.createRun();
    const result = await run.start({
      inputData: {
        strategies: ["sma_cross", "buy_and_hold"],
        symbols: ["BTC/USDT", "ETH/USDT"],
        venue: "binance",
        timeframe: "1h",
        from_ts: "2024-01-01T00:00:00Z",
        to_ts: "2024-12-31T00:00:00Z",
        initial_cash: 10_000,
        fee_rate: 0.001,
      },
    });

    expect(result.status).toBe("success");
    if (result.status !== "success") return;
    expect(calls).toHaveLength(4);
    expect(result.result.summary.ok).toBe(3);
    expect(result.result.summary.errored).toBe(1);

    const errored = result.result.reports.find((r) => r.error !== null);
    expect(errored?.job.strategy_id).toBe("sma_cross");
    expect(errored?.job.symbol).toBe("ETH/USDT");
    expect(errored?.report).toBeNull();
    // Pareto 只看 ok 的 3 个
    expect(result.result.pareto.length).toBeGreaterThanOrEqual(1);
    expect(result.result.pareto.length).toBeLessThanOrEqual(3);
  });

  // ────────────────────────────────────────────────────────────────────
  // D-9 · candidate 路径 grid（与 strategies 并列）
  // ────────────────────────────────────────────────────────────────────

  it("expands candidateIds × symbols into N jobs (D-9 candidate grid)", async () => {
    const calls = mockPaperBacktest({});
    const c1 = "550e8400-e29b-41d4-a716-446655440001";
    const c2 = "550e8400-e29b-41d4-a716-446655440002";

    const wf = mastra.getWorkflow("backtest_grid");
    const run = await wf.createRun();
    const result = await run.start({
      inputData: {
        candidateIds: [c1, c2],
        symbols: ["BTC/USDT", "ETH/USDT"],
        venue: "binance",
        timeframe: "1h",
        from_ts: "2024-01-01T00:00:00Z",
        to_ts: "2024-12-31T00:00:00Z",
        initial_cash: 10_000,
        fee_rate: 0.001,
      },
    });

    expect(result.status).toBe("success");
    if (result.status !== "success") return;
    expect(calls).toHaveLength(4);
    // 所有 4 个 job 都走 candidate 分支（HTTP body 必有 candidate_id 字段非空）
    for (const c of calls) {
      const body = c.body as { strategy_id: string | null; candidate_id: string | null };
      expect(body.candidate_id).not.toBeNull();
      expect(body.strategy_id).toBeFalsy();
    }
    // shim 透传 D-9 字段
    const okReports = result.result.reports.filter((r) => r.report !== null);
    expect(okReports).toHaveLength(4);
    for (const r of okReports) {
      expect(r.report!.candidate_id).not.toBeNull();
      expect(r.report!.fitness).toBe(1.2);
      expect(r.report!.baseline).not.toBeNull();
      expect(r.report!.baseline!.strategy_id).toBe("buy_and_hold");
      expect(r.report!.strategy_id.startsWith("candidate:")).toBe(true);
    }
  });

  it("supports mixed strategies + candidateIds in a single grid", async () => {
    const calls = mockPaperBacktest({});
    const c1 = "550e8400-e29b-41d4-a716-446655440001";

    const wf = mastra.getWorkflow("backtest_grid");
    const run = await wf.createRun();
    const result = await run.start({
      inputData: {
        strategies: ["sma_cross"],
        candidateIds: [c1],
        symbols: ["BTC/USDT"],
        venue: "binance",
        timeframe: "1h",
        from_ts: "2024-01-01T00:00:00Z",
        to_ts: "2024-12-31T00:00:00Z",
        initial_cash: 10_000,
        fee_rate: 0.001,
      },
    });

    expect(result.status).toBe("success");
    if (result.status !== "success") return;
    expect(calls).toHaveLength(2);
    // 一条 strategy 一条 candidate
    const builtinCall = calls.find(
      (c) => (c.body as { strategy_id: string | null }).strategy_id === "sma_cross",
    );
    const candidateCall = calls.find(
      (c) => (c.body as { candidate_id: string | null }).candidate_id === c1,
    );
    expect(builtinCall).toBeDefined();
    expect(candidateCall).toBeDefined();
    // 报告 shim：内置路径 baseline=null, candidate 路径 baseline 非空
    const builtinShim = result.result.reports.find(
      (r) => r.report?.strategy_id === "sma_cross",
    )?.report;
    const candidateShim = result.result.reports.find(
      (r) => r.report?.candidate_id === c1,
    )?.report;
    expect(builtinShim?.baseline).toBeNull();
    expect(candidateShim?.baseline).not.toBeNull();
  });

  // D-9 multi-market 回归：grid SymbolSchema 不应拒 crypto 之外的市场
  it.each([
    ["crypto", "BTC/USDT"],
    ["us_stock", "AAPL"],
    ["us_index", "^GSPC"],
    ["jp_index", "^N225"],
    ["cn_stock_akshare", "sh.600519"],
    ["hk_stock_akshare", "hk.00700"],
    ["kr_stock_yfinance", "005930.KS"],
    ["fred_macro", "DFF"],
  ])("GridInputSchema accepts multi-market symbol: %s -> %s", (_label, sym) => {
    const parsed = GridInputSchema.safeParse({
      strategies: ["sma_cross"],
      symbols: [sym],
      venue: "binance", // venue 字段对 schema 校验无影响，只是默认值
      timeframe: "1h",
    });
    expect(parsed.success).toBe(true);
  });

  it("rejects grid with neither strategies nor candidateIds", async () => {
    const wf = mastra.getWorkflow("backtest_grid");
    const run = await wf.createRun();
    const result = await run.start({
      inputData: {
        // 故意不传 strategies / candidateIds
        symbols: ["BTC/USDT"],
        venue: "binance",
        timeframe: "1h",
        initial_cash: 10_000,
        fee_rate: 0.001,
      } as never,
    });
    // schema superRefine 拒
    expect(result.status).not.toBe("success");
  });
});
