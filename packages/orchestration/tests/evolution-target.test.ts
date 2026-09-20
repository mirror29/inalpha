import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { clearSettings, setSettings } from "../src/config.js";
import { evolverResolveTargetTool, evolverStartLoopTool } from "../src/tools/index.js";

const TOKEN = "owner-token";
const TARGET_ID = "11111111-1111-4111-8111-111111111111";
const RELATED_ID = "22222222-2222-4222-8222-222222222222";
const BACKTEST_ID = "33333333-3333-4333-8333-333333333333";

const frozenConfig = {
  venue: "binance",
  symbol: "BTC/USDT:USDT",
  timeframe: "1h",
  from_ts: "2026-01-01T00:00:00.000Z",
  as_of: "2026-07-01T00:00:00.000Z",
  initial_cash: 10_000,
  fee_rate: 0.001,
  trading_mode: "perp",
  leverage: 3,
  params: { trade_size: 3 },
  funding_rate: 0.001,
};

beforeEach(() => {
  setSettings({
    dataServiceUrl: "http://data.test",
    paperServiceUrl: "http://paper.test",
    researchServiceUrl: "http://research.test",
    factorServiceUrl: "http://factor.test",
    evolverServiceUrl: "http://evolver.test",
    jwtSecret: "test-secret-32-chars-or-more-xxxxxxx",
    jwtAlgorithm: "HS256",
  });
});

afterEach(() => {
  clearSettings();
  vi.unstubAllGlobals();
});

const ctx = { requestContext: { authToken: TOKEN } } as never;

describe("evolver.resolve_target", () => {
  it("reads loop detail without launching research or requesting model credentials", async () => {
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      expect(url).toBe(`http://evolver.test/api/v1/evolution-loops/${TARGET_ID}`);
      expect(init?.method ?? "GET").toBe("GET");
      return new Response(JSON.stringify({ loop_id: TARGET_ID, status: "baseline_ready", campaign_id: null }), {
        headers: { "Content-Type": "application/json" },
      });
    }));
    expect(await resolve("evolution_loop", TARGET_ID)).toMatchObject({
      next_action: "inspect_loop", start_input: null,
      evidence: { loop_id: TARGET_ID, loop_status: "baseline_ready" },
    });
  });
  it("reuses a workflow without a model grant or new snapshot even when new launches are disabled", async () => {
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      expect(init?.method ?? "GET").toBe("GET");
      const body = url.includes("/evolution-loops/for-target")
        ? { loop_id: RELATED_ID, status: "waiting_forward" }
        : routeBody(url);
      return new Response(JSON.stringify(body), { headers: { "Content-Type": "application/json" } });
    }));
    const result = await evolverStartLoopTool.execute!(
      { targetKind: "strategy_candidate", targetId: TARGET_ID, budget: 4 }, ctx,
    );
    expect(result).toMatchObject({ loop_id: RELATED_ID, loop_status: "waiting_forward" });
  });

  it("starts a durable workflow when available and resumes it from either detail page", async () => {
    let active = false;
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      const body = url.endsWith("/capabilities")
        ? { durable_loop_enabled: true }
        : url.includes("/evolution-loops/for-target")
          ? active ? { loop_id: RELATED_ID, status: "campaign_running", e1_run_id: TARGET_ID } : null
          : routeBody(url);
      return new Response(JSON.stringify(body), { headers: { "Content-Type": "application/json" } });
    }));
    expect(await resolve("strategy_candidate", TARGET_ID)).toMatchObject({ next_action: "start_loop" });
    active = true;
    for (const kind of ["strategy_candidate", "e1_run"]) {
      expect(await resolve(kind, TARGET_ID)).toMatchObject({
        next_action: "inspect_loop", start_input: null,
        evidence: { loop_id: RELATED_ID, loop_status: "campaign_running" },
      });
    }
  });

  it("normalizes all supported detail targets behind one owner-scoped interface", async () => {
    const calls: string[] = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      calls.push(url);
      expect((init?.headers as Record<string, string>).Authorization).toBe(`Bearer ${TOKEN}`);
      const body = routeBody(url);
      return new Response(JSON.stringify(body), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }));

    const candidate = await resolve("strategy_candidate", TARGET_ID);
    expect(candidate).toMatchObject({
      next_action: "start_e1",
      start_input: {
        seedStrategyId: `candidate:${TARGET_ID}`,
        config: frozenConfig,
      },
    });

    const runner = await resolve("paper_runner", TARGET_ID);
    expect(runner).toMatchObject({
      next_action: "start_e1",
      start_input: { seedStrategyId: `candidate:${RELATED_ID}` },
    });

    const backtest = await resolve("backtest_run", BACKTEST_ID);
    expect(backtest).toMatchObject({
      next_action: "start_e1",
      start_input: { seedStrategyId: `candidate:${TARGET_ID}` },
    });

    const e1Run = await resolve("e1_run", TARGET_ID);
    expect(e1Run).toMatchObject({
      next_action: "start_e2",
      start_input: { sourceRunId: TARGET_ID, config: frozenConfig },
    });

    const e1Candidate = await resolve("e1_candidate", TARGET_ID);
    expect(e1Candidate).toMatchObject({
      next_action: "start_e1",
      start_input: { seedStrategyId: `evolution_candidate:${TARGET_ID}` },
    });

    const campaign = await resolve("e2_campaign", TARGET_ID);
    expect(campaign).toMatchObject({
      next_action: "inspect_e2",
      start_input: null,
      evidence: { campaign_id: TARGET_ID, active_generation: 3 },
    });

    expect(calls).toContain(`http://paper.test/backtest_runs/${BACKTEST_ID}`);
    expect(calls).toContain(`http://evolver.test/api/v1/campaigns/${TARGET_ID}`);
  });

  it("blocks rejected candidates without inventing missing market inputs", async () => {
    vi.stubGlobal("fetch", vi.fn(async (url: string) => new Response(JSON.stringify(url.includes("/evolution-loops/for-target") ? null : {
      id: TARGET_ID,
      status: "rejected",
      last_backtest_run_id: null,
      fitness: null,
    }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    })));

    const result = await resolve("strategy_candidate", TARGET_ID);
    expect(result).toMatchObject({
      next_action: "blocked",
      start_input: null,
      blockers: ["strategy_candidate_rejected"],
    });
  });

  it("aligns an inherited intraday window to the canonical bar grid", async () => {
    vi.stubGlobal("fetch", vi.fn(async (url: string) => new Response(JSON.stringify(
      url.includes("/evolution-loops/for-target") ? null
        : url.endsWith("/capabilities") ? { durable_loop_enabled: false }
        : url.includes("strategy_candidates")
        ? {
            id: TARGET_ID,
            status: "candidate",
            last_backtest_run_id: BACKTEST_ID,
            fitness: 0.8,
          }
        : {
            run_id: BACKTEST_ID,
            strategy_code: `candidate:${TARGET_ID}`,
            config: {
              ...frozenConfig,
              timeframe: "4h",
              from_ts: "2025-09-20T09:19:17.199Z",
              as_of: "2026-09-20T09:19:17.199Z",
            },
            metrics: {},
            status: "done",
          },
    ), { status: 200, headers: { "Content-Type": "application/json" } })));

    const result = await resolve("strategy_candidate", TARGET_ID);

    expect(result.start_input).toMatchObject({
      config: {
        from_ts: "2025-09-20T08:00:00.000Z",
        as_of: "2026-09-20T09:19:17.199Z",
      },
    });
  });
});

async function resolve(targetKind: string, targetId: string) {
  return await evolverResolveTargetTool.execute!(
    { targetKind, targetId } as never,
    ctx,
  ) as {
    next_action: string;
    start_input: Record<string, unknown> | null;
    blockers: string[];
    evidence: Record<string, unknown>;
  };
}

function routeBody(url: string): Record<string, unknown> | null {
  if (url.endsWith("/capabilities")) return { durable_loop_enabled: false };
  if (url.includes("/evolution-loops/for-target")) return null;
  if (url === `http://paper.test/strategy_candidates/${TARGET_ID}`) {
    return {
      id: TARGET_ID,
      status: "candidate",
      last_backtest_run_id: BACKTEST_ID,
      fitness: 1.2,
    };
  }
  if (url === `http://paper.test/strategy_candidates/${RELATED_ID}`) {
    return {
      id: RELATED_ID,
      status: "promoted",
      last_backtest_run_id: BACKTEST_ID,
      fitness: 1.1,
    };
  }
  if (url === `http://paper.test/backtest_runs/${BACKTEST_ID}`) {
    return {
      run_id: BACKTEST_ID,
      strategy_code: `candidate:${TARGET_ID}`,
      config: frozenConfig,
      metrics: { fitness: 1.2 },
      status: "done",
    };
  }
  if (url === `http://paper.test/strategy_runs/${TARGET_ID}`) {
    return {
      id: TARGET_ID,
      candidate_id: RELATED_ID,
      status: "running",
      venue: "binance",
      symbol: "BTC/USDT:USDT",
      timeframe: "1h",
      allocation: 5_000,
      last_bar_ts: "2026-07-01T00:00:00Z",
      cumulative_pnl: 123.45,
      started_at: "2026-06-01T00:00:00Z",
    };
  }
  if (url === `http://evolver.test/api/v1/candidates/${TARGET_ID}`) {
    return {
      candidate_id: TARGET_ID,
      run_id: RELATED_ID,
      slot: 2,
      outcome: "succeeded",
      source_code: "def generate_signals(): pass",
      fitness: 1.3,
      overfitting_risk: "low",
    };
  }
  if (url === `http://evolver.test/api/v1/runs/${RELATED_ID}`) {
    return {
      run_id: RELATED_ID,
      status: "completed",
      config: frozenConfig,
      attempted: 4,
      succeeded: 3,
      rejected: 1,
      llm_cost_usd: 0.5,
    };
  }
  if (url === `http://evolver.test/api/v1/runs/${TARGET_ID}`) {
    return {
      run_id: TARGET_ID,
      status: "completed",
      config: frozenConfig,
      attempted: 4,
      succeeded: 3,
      rejected: 1,
      llm_cost_usd: 0.5,
    };
  }
  if (url === `http://evolver.test/api/v1/campaigns/${TARGET_ID}`) {
    return {
      campaign_id: TARGET_ID,
      status: "replaying",
      active_generation: 3,
      max_generations: 5,
      event_snapshot_id: BACKTEST_ID,
      llm_cost_usd: 1.25,
    };
  }
  throw new Error(`unexpected test URL: ${url}`);
}
