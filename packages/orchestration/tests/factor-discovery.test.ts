/**
 * D-12 · 因子发现 L1 第一块：factor.evaluate_candidate tool + 表达式外围 hook。
 *
 * 真审计在 factor service（expression.py），这里只测：
 * - tool 入参 camelCase → snake_case 透传 /custom/score
 * - factor-expression-audit hook 的廉价拦截（负 lag / 未来命名 / 超长 / 注入串）
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { clearSettings, setSettings } from "../src/config.js";
import {
  HookRunner,
  defaultFactorExpressionAuditRegistration,
} from "../src/hooks/index.js";
import {
  factorEvaluateCandidateTool,
  factorListCandidatesTool,
  factorProposeTool,
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

const ctx = (authToken: string | undefined = TEST_TOKEN): never =>
  ({ requestContext: { authToken } }) as never;

describe("factor.evaluate_candidate", () => {
  it("POSTs /custom/score with snake_case body", async () => {
    let capturedUrl = "";
    let capturedBody = "";
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        capturedUrl = url;
        capturedBody = (init?.body as string) ?? "";
        return new Response(
          JSON.stringify({
            venue: "binance",
            symbol: "BTC/USDT",
            timeframe: "1h",
            as_of: "2026-06-11T00:00:00Z",
            horizon_bars: 5,
            bars_used: 720,
            available: true,
            reason: null,
            expression: "($close - Ref($close, 5)) / Ref($close, 5)",
            factor: null,
            ic_pvalue: 0.03,
            top_correlated: [],
            max_corr: null,
            is_likely_redundant: false,
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }),
    );

    const result = await factorEvaluateCandidateTool.execute!(
      {
        expression: "($close - Ref($close, 5)) / Ref($close, 5)",
        venue: "binance",
        symbol: "BTC/USDT",
        timeframe: "1h",
        lookbackBars: 720,
        horizonBars: 5,
      } as never,
      ctx(),
    );

    expect(capturedUrl).toContain("/custom/score");
    const body = JSON.parse(capturedBody) as Record<string, unknown>;
    expect(body.expression).toContain("Ref($close, 5)");
    expect(body.lookback_bars).toBe(720);
    expect(body.horizon_bars).toBe(5);
    expect((result as { ic_pvalue: number }).ic_pvalue).toBe(0.03);
  });
});

describe("factor.propose / factor.list_candidates", () => {
  it("propose POSTs /candidates with snake_case body", async () => {
    let capturedUrl = "";
    let capturedBody = "";
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        capturedUrl = url;
        capturedBody = (init?.body as string) ?? "";
        return new Response(
          JSON.stringify({
            candidate_id: "550e8400-e29b-41d4-a716-446655440000",
            expression_hash: "abc123",
            created: true,
            status: "pending_review",
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }),
    );

    const result = await factorProposeTool.execute!(
      {
        expression: "Rank($volume, 20) * Sign(Delta($close, 1))",
        hypothesis: "放量伴随方向时短期动量延续：成交量确认价格信息的扩散速度",
        nTested: 7,
        testResults: { rank_ic: 0.06, decay_state: "stable" },
      } as never,
      ctx(),
    );

    expect(capturedUrl).toContain("/candidates");
    const body = JSON.parse(capturedBody) as Record<string, unknown>;
    expect(body.hypothesis).toContain("放量");
    expect(body.n_tested).toBe(7);
    expect((body.test_results as Record<string, unknown>).rank_ic).toBe(0.06);
    expect((result as { created: boolean }).created).toBe(true);
  });

  it("propose inputSchema enforces hypothesis >= 20 chars", () => {
    const r = factorProposeTool.inputSchema!.safeParse({
      expression: "Mean($close, 20)",
      hypothesis: "太短",
    });
    expect(r.success).toBe(false);
  });

  it("list_candidates GETs /candidates with status filter", async () => {
    let capturedUrl = "";
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        capturedUrl = url;
        return new Response(JSON.stringify([]), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }),
    );

    await factorListCandidatesTool.execute!(
      { status: "pending_review", limit: 10 } as never,
      ctx(),
    );
    expect(capturedUrl).toContain("/candidates");
    expect(capturedUrl).toContain("status=pending_review");
  });
});

describe("factor-expression-audit hook", () => {
  function runHook(expression: string, toolName = "factor.evaluate_candidate") {
    const runner = new HookRunner();
    runner.register(defaultFactorExpressionAuditRegistration());
    return runner.run("PreToolUse", {
      toolName,
      toolInput: { expression },
    });
  }

  it("denies negative lag (lookahead)", async () => {
    const r = await runHook("Ref($close, -3) / $close");
    expect(r.permissionOverride).toBe("deny");
    expect(r.message).toContain("LOOKAHEAD");
  });

  it("denies future-semantic naming", async () => {
    const r = await runHook("$close / future_price");
    expect(r.permissionOverride).toBe("deny");
    expect(r.message).toContain("FUTURE_NAMING");
  });

  it("denies oversized expression", async () => {
    const r = await runHook("$close + ".repeat(400) + "$close");
    expect(r.permissionOverride).toBe("deny");
    expect(r.message).toContain("TOO_LARGE");
  });

  it("denies injection literals", async () => {
    const r = await runHook("eval('x') + $close");
    expect(r.permissionOverride).toBe("deny");
    expect(r.message).toContain("INJECTION");
  });

  it("passes a normal expression", async () => {
    const r = await runHook("Rank($volume, 20) * Sign(Delta($close, 1))");
    expect(r.permissionOverride).toBeUndefined();
  });

  it("does not match other tools", async () => {
    const r = await runHook("Ref($close, -3)", "factor.score");
    expect(r.permissionOverride).toBeUndefined();
  });
});
