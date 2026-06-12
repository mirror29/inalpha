/**
 * factor_discovery workflow 验证（D-12 · 因子发现 L1·P3）。
 *
 * 1. **bhAdjust 纯函数**：BH 校正的排序/单调/截断
 * 2. **端到端 via mastra.getWorkflow**：mock factor /custom/score 与 /candidates，
 *    验证 BH 拒绝 / 冗余剪枝 / decaying 门 / propose 落库 body / fail-fast
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { clearSettings, setSettings } from "../src/config.js";
import { mastra } from "../src/mastra/index.js";
import { bhAdjust } from "../src/mastra/workflows/factor-discovery.js";

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

// ────────────────────────────────────────────────────────────────────
// Pure: bhAdjust
// ────────────────────────────────────────────────────────────────────

describe("bhAdjust", () => {
  it("single p value scales by m=1 (unchanged)", () => {
    expect(bhAdjust([0.04])).toEqual([0.04]);
  });

  it("classic BH: smallest p scaled by m, preserves input order", () => {
    // p=[0.01, 0.04, 0.03], m=3 → 排序 [0.01,0.03,0.04]
    // ranked: 0.01*3/1=0.03, 0.03*3/2=0.045, 0.04*3/3=0.04 → cummin from right:
    // [0.03, 0.04, 0.04] → 回原序 [0.03, 0.04, 0.04]
    const out = bhAdjust([0.01, 0.04, 0.03]);
    expect(out[0]).toBeCloseTo(0.03, 10);
    expect(out[1]).toBeCloseTo(0.04, 10);
    expect(out[2]).toBeCloseTo(0.04, 10);
  });

  it("clamps to [0, 1]", () => {
    const out = bhAdjust([0.9, 0.95, 0.99]);
    expect(Math.max(...out)).toBeLessThanOrEqual(1);
  });

  it("empty input → empty output", () => {
    expect(bhAdjust([])).toEqual([]);
  });

  it("m 覆盖 = nTested：批中含评估失败的尝试时用整批大小校正", () => {
    // 2 个观测 p 值，但整批 5 次尝试（3 次评估失败）→ m=5。
    // 排序 [0.01, 0.02]，ranked: 0.01*5/1=0.05, 0.02*5/2=0.05 → cummin [0.05,0.05]
    const out = bhAdjust([0.02, 0.01], 5);
    expect(out[0]).toBeCloseTo(0.05, 10); // 原序第 0 项 = 0.02
    expect(out[1]).toBeCloseTo(0.05, 10); // 原序第 1 项 = 0.01
  });

  it("m 覆盖在 [0,1] 截断只做一次（缩放后），不会先 clamp 再放大", () => {
    // p=0.3，m=5：BH = min(1, 0.3*5/1)=1。旧实现先 bhAdjust([0.3])=0.3 再 *5=1.5→clamp，
    // 结果同为 1，但单值不暴露差异；多值时 cummin 顺序受 clamp 影响才会偏，这里验单调正确。
    const out = bhAdjust([0.3, 0.05], 5);
    expect(Math.max(...out)).toBeLessThanOrEqual(1);
    expect(out[1]).toBeLessThanOrEqual(out[0]); // 小 p（0.05）调整后不大于大 p（0.3）
  });
});

// ────────────────────────────────────────────────────────────────────
// End-to-end via mastra.getWorkflow + fetch mock
// ────────────────────────────────────────────────────────────────────

function customScoreResp(opts: {
  rankIc: number;
  pvalue: number;
  maxCorr?: number;
  decayState?: string;
  lowConfidence?: boolean;
}) {
  return {
    venue: "binance",
    symbol: "BTC/USDT",
    timeframe: "1h",
    as_of: "2026-06-11T00:00:00Z",
    horizon_bars: 5,
    bars_used: 720,
    available: true,
    reason: null,
    expression: "x",
    factor: {
      factor_id: "custom.x",
      source: "custom",
      name: "x",
      kind: "custom",
      value: 1,
      rank_ic: opts.rankIc,
      rank_ic_recent: opts.rankIc,
      icir: 0.5,
      turnover: 0.2,
      sample_size: 700,
      quantile_returns: [],
      long_short_return: 0,
      direction: 1,
      strength: 0.5,
      low_confidence: opts.lowConfidence ?? false,
      decay_state: opts.decayState ?? "stable",
    },
    ic_pvalue: opts.pvalue,
    top_correlated: [{ factor_id: "qlib.roc_5", corr: opts.maxCorr ?? 0.3 }],
    max_corr: opts.maxCorr ?? 0.3,
    is_likely_redundant: (opts.maxCorr ?? 0.3) >= 0.85,
  };
}

/** 按表达式路由 mock 响应；记录 propose 请求体。 */
function mockFactorService(
  scoreByExpr: Record<string, ReturnType<typeof customScoreResp>>,
) {
  const proposeBodies: Record<string, unknown>[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const body = init?.body ? (JSON.parse(init.body as string) as Record<string, unknown>) : {};
      if (url.includes("/custom/score")) {
        const resp = scoreByExpr[body.expression as string];
        if (!resp) {
          return new Response(
            JSON.stringify({ code: "FACTOR_EXPRESSION_INVALID", message: "bad expr" }),
            { status: 400, headers: { "Content-Type": "application/json" } },
          );
        }
        return new Response(JSON.stringify(resp), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      if (url.includes("/candidates")) {
        proposeBodies.push(body);
        return new Response(
          JSON.stringify({
            candidate_id: "550e8400-e29b-41d4-a716-446655440000",
            expression_hash: "abc",
            created: true,
            status: "pending_review",
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }
      throw new Error(`unexpected url ${url}`);
    }),
  );
  return proposeBodies;
}

const HYP = "动量效应：信息扩散有时滞，近期趋势短期内倾向延续，这是被广泛记录的行为偏差";

async function runDiscovery(input: Record<string, unknown>) {
  const wf = mastra.getWorkflow("factor_discovery");
  const run = await wf.createRun();
  return await run.start({ inputData: input as never });
}

describe("factor_discovery workflow", () => {
  it("strong candidate passes all gates and is proposed with batch metadata", async () => {
    const proposeBodies = mockFactorService({
      "Mean($close, 20) / $close": customScoreResp({ rankIc: 0.12, pvalue: 0.001 }),
    });

    const result = await runDiscovery({
      candidates: [{ expression: "Mean($close, 20) / $close", hypothesis: HYP }],
      venue: "binance",
      symbol: "BTC/USDT",
      timeframe: "1h",
    });

    expect(result.status).toBe("success");
    const out = (result as { result: { verdicts: { outcome: string }[]; summary: { proposed: number } } }).result;
    expect(out.summary.proposed).toBe(1);
    expect(out.verdicts[0].outcome).toBe("proposed");
    // propose body 带审计锚点
    expect(proposeBodies).toHaveLength(1);
    expect(proposeBodies[0].n_tested).toBe(1);
    expect(proposeBodies[0].batch_id).toBeTruthy();
    const tr = proposeBodies[0].test_results as Record<string, unknown>;
    expect(tr.rank_ic).toBe(0.12);
    expect(tr.adjusted_p).toBeDefined();
  });

  it("redundant / decaying / weak-p candidates are rejected (BH with m=batch)", async () => {
    const proposeBodies = mockFactorService({
      "Mean($close, 20)": customScoreResp({ rankIc: 0.12, pvalue: 0.001 }),
      "Mean($close, 21)": customScoreResp({ rankIc: 0.11, pvalue: 0.001, maxCorr: 0.95 }),
      "Mean($close, 22)": customScoreResp({ rankIc: 0.08, pvalue: 0.002, decayState: "decaying" }),
      "Mean($close, 23)": customScoreResp({ rankIc: 0.02, pvalue: 0.2 }),
    });

    const result = await runDiscovery({
      candidates: [
        { expression: "Mean($close, 20)", hypothesis: HYP },
        { expression: "Mean($close, 21)", hypothesis: HYP },
        { expression: "Mean($close, 22)", hypothesis: HYP },
        { expression: "Mean($close, 23)", hypothesis: HYP },
      ],
      venue: "binance",
      symbol: "BTC/USDT",
    });

    expect(result.status).toBe("success");
    const out = (result as {
      result: { verdicts: { expression: string; outcome: string }[] };
    }).result;
    const byExpr = Object.fromEntries(out.verdicts.map((v) => [v.expression, v.outcome]));
    expect(byExpr["Mean($close, 20)"]).toBe("proposed");
    expect(byExpr["Mean($close, 21)"]).toBe("rejected_redundant");
    expect(byExpr["Mean($close, 22)"]).toBe("rejected_decaying");
    expect(byExpr["Mean($close, 23)"]).toBe("rejected_adjusted_p");
    expect(proposeBodies).toHaveLength(1);
    expect(proposeBodies[0].n_tested).toBe(4); // m = 整批，不是幸存者数
  });

  it("fail-fast: negative lag in any candidate rejects the whole batch", async () => {
    mockFactorService({});
    const result = await runDiscovery({
      candidates: [
        { expression: "Mean($close, 20)", hypothesis: HYP },
        { expression: "Ref($close, -3)", hypothesis: HYP },
      ],
      venue: "binance",
      symbol: "BTC/USDT",
    });
    expect(result.status).toBe("failed");
    const err = (result as { error: unknown }).error;
    const msg =
      err instanceof Error ? err.message : JSON.stringify(err) + String(err);
    expect(msg).toContain("lookahead");
  });

  it("eval failure is isolated per item but counted in m", async () => {
    const proposeBodies = mockFactorService({
      "Mean($close, 20)": customScoreResp({ rankIc: 0.12, pvalue: 0.001 }),
      // "Foo($close, 5)" 不在 map → mock 返 400
    });
    const result = await runDiscovery({
      candidates: [
        { expression: "Mean($close, 20)", hypothesis: HYP },
        { expression: "Foo($close, 5)", hypothesis: HYP },
      ],
      venue: "binance",
      symbol: "BTC/USDT",
    });
    expect(result.status).toBe("success");
    const out = (result as {
      result: { summary: { errored: number; proposed: number } };
    }).result;
    expect(out.summary.errored).toBe(1);
    expect(out.summary.proposed).toBe(1);
    expect(proposeBodies[0].n_tested).toBe(2); // 失败的尝试也计入选择效应背景
  });

  it("propose=false dry-runs without writing candidates", async () => {
    const proposeBodies = mockFactorService({
      "Mean($close, 20)": customScoreResp({ rankIc: 0.12, pvalue: 0.001 }),
    });
    const result = await runDiscovery({
      candidates: [{ expression: "Mean($close, 20)", hypothesis: HYP }],
      venue: "binance",
      symbol: "BTC/USDT",
      propose: false,
    });
    expect(result.status).toBe("success");
    const out = (result as { result: { verdicts: { outcome: string }[] } }).result;
    expect(out.verdicts[0].outcome).toBe("evaluated_only");
    expect(proposeBodies).toHaveLength(0);
  });
});
