/**
 * Wired tools 集成验证 —— hook + permission + plan-exec 协同作用的"trip wires"。
 *
 * 这里不是单测，是把 ADR-0010 + ADR-0011 + ADR-0012 三者拼起来跑一遍：
 *
 * - audit-log hook 在 PostToolUse 自动 fire
 * - 默认 permission 把任何 "submit_order"-shape 工具拒掉
 * - permission 允许 trade.* 全套
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { clearSettings, setSettings } from "../src/config.js";
import { HookRunner } from "../src/hooks/index.js";
import {
  DEFAULT_PERMISSIONS,
  PermissionEngine,
} from "../src/permissions/index.js";
import { wireToolList } from "../src/mastra/wired-tools.js";
import {
  dataBackfillBarsTool,
  paperRunBacktestTool,
} from "../src/tools/index.js";
import { withHooks } from "../src/hooks/index.js";

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

describe("wireToolList · permission + audit-log integration", () => {
  it("wires data.backfill_bars to a hook-wrapped tool that emits PostToolUse audit", async () => {
    // 默认 audit matcher 不包含纯读 data.get_bars（避免每次查 K 线都 log），
    // 但 backfill 是写操作，应该 log。
    const records: Record<string, unknown>[] = [];

    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(
          JSON.stringify({
            venue: "binance",
            symbol: "BTC/USDT",
            timeframe: "1h",
            bars_fetched: 24,
            bars_inserted: 24,
            from_ts: "2026-01-01T00:00:00Z",
            to_ts: "2026-01-02T00:00:00Z",
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      ),
    );

    const [wrapped] = wireToolList([dataBackfillBarsTool], { auditSink: (r) => records.push(r) });

    await wrapped!.execute!(
      {
        venue: "binance",
        symbol: "BTC/USDT",
        timeframe: "1h",
        fromTs: "2026-01-01T00:00:00Z",
        toTs: "2026-01-02T00:00:00Z",
      },
      { requestContext: { authToken: "test" } },
    );

    expect(records).toHaveLength(1);
    expect(records[0]!.event).toBe("PostToolUse");
    expect(records[0]!.tool).toBe("data.backfill_bars");
  });

  it("permissionEngine denies paper.submit_order_intent (ADR-0012 forbidden path)", () => {
    const engine = new PermissionEngine(DEFAULT_PERMISSIONS);
    // 无论参数，paper.submit_order_intent 整条路径都该 deny（强制走 trade.create_plan）
    expect(engine.authorize("paper.submit_order_intent", { notional: 50 }).decision).toBe("deny");
    expect(engine.authorize("paper.submit_order_intent", { notional: 50_000 }).decision).toBe("deny");
    expect(engine.authorize("paper.submit_order_intent", {}).decision).toBe("deny");
  });

  it("a synthetic 'paper.submit_order_intent' tool gets blocked through wireToolList", async () => {
    // 构造一个 mock tool 模拟"被禁路径"——permission engine 应直接挡掉
    const forbiddenTool = {
      id: "paper.submit_order_intent",
      description: "synthetic",
      execute: vi.fn(),
    };

    const [wrapped] = wireToolList([forbiddenTool], {
      hookRunner: new HookRunner(),
      permissionEngine: new PermissionEngine(DEFAULT_PERMISSIONS),
    });

    const out = (await wrapped!.execute!({ notional: 50 })) as {
      isError: boolean;
      deniedBy: string;
    };
    expect(out.isError).toBe(true);
    expect(out.deniedBy).toBe("permission");
    expect(forbiddenTool.execute).not.toHaveBeenCalled();
  });

  it("hook can override permission to allow execution", async () => {
    const runner = new HookRunner();
    runner.register({
      id: "force-allow",
      event: "PreToolUse",
      handler: () => ({ permissionOverride: "allow" }),
    });

    const tool = {
      id: "paper.submit_order_intent",
      description: "synthetic",
      execute: vi.fn().mockResolvedValue({ ran: true }),
    };

    const [wrapped] = wireToolList([tool], {
      hookRunner: runner,
      permissionEngine: new PermissionEngine(DEFAULT_PERMISSIONS),
    });

    const out = await wrapped!.execute!({ notional: 50 });
    expect(tool.execute).toHaveBeenCalled();
    expect(out).toMatchObject({ ran: true });
  });

  it("paper.run_backtest is allowed by default and executes", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(
          JSON.stringify({
            strategy_id: "sma_cross",
            venue: "binance",
            symbol: "BTC/USDT",
            timeframe: "1h",
            initial_cash: 10000,
            final_equity: 10100,
            total_return_pct: 1.0,
            num_trades: 2,
            total_fees: 1.0,
            num_bars_processed: 100,
            period_start: "2026-01-01T00:00:00Z",
            period_end: "2026-01-05T00:00:00Z",
            sharpe: 1.2,
            sortino: 1.5,
            max_drawdown_pct: 2.0,
            win_rate: 50.0,
            equity_curve: [],
            final_positions: [],
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      ),
    );

    const [wrapped] = wireToolList([paperRunBacktestTool]);
    const out = await wrapped!.execute!(
      {
        strategyId: "sma_cross",
        params: {},
        venue: "binance",
        symbol: "BTC/USDT",
        timeframe: "1h",
        fromTs: "2026-01-01T00:00:00Z",
        toTs: "2026-01-05T00:00:00Z",
        initialCash: 10000,
        feeRate: 0.001,
      },
      { requestContext: { authToken: "test" } },
    );

    // 应该带 hookMessage 标注 audit；同时含原始字段
    expect(out).toMatchObject({
      num_trades: 2,
      sharpe: 1.2,
    });
  });

  it("ask-decision yields pending-approval marker (no executor in D-8a)", async () => {
    const tool = {
      id: "risk.update_config",
      description: "synthetic",
      execute: vi.fn(),
    };

    const [wrapped] = wireToolList([tool], {
      hookRunner: new HookRunner(),
      permissionEngine: new PermissionEngine(DEFAULT_PERMISSIONS),
    });

    const out = (await wrapped!.execute!({})) as { isError: boolean; deniedBy: string };
    expect(out.isError).toBe(true);
    expect(out.deniedBy).toBe("permission-ask-pending");
    expect(tool.execute).not.toHaveBeenCalled();
  });
});

describe("wireToolList · withHooks re-export sanity", () => {
  it("returns tool with original id (Mastra Agent.tools key matches)", () => {
    const t = { id: "x.y", execute: async () => 1 };
    const [w] = wireToolList([t], {
      hookRunner: new HookRunner(),
      permissionEngine: new PermissionEngine({ defaultMode: "allow", allow: [], ask: [], deny: [] }),
    });
    expect(w!.id).toBe("x.y");
  });

  it("withHooks-only path also works for ad-hoc tests", async () => {
    const runner = new HookRunner();
    const t = withHooks({ id: "t", execute: async () => ({ ok: true }) }, { runner });
    const out = await t.execute!({});
    expect(out).toEqual({ ok: true });
  });
});
