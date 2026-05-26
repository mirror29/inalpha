/**
 * `risk.*` MCP tool 单测（ADR-0006 Step 5）。
 *
 * 覆盖 3 个 tool 经 RiskClient → HTTP → paper service `/risk/*` 路径。
 * fetch 全 mock，验证：URL / method / body / 返回 schema。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { clearSettings, setSettings } from "../src/config.js";
import {
  riskDescribeRulesTool,
  riskListLocksTool,
  riskUnlockTool,
} from "../src/tools/risk.js";

const TEST_TOKEN = "test-token";

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

// ────────────────────────────────────────────────────────────────────
// risk.describe_rules
// ────────────────────────────────────────────────────────────────────

describe("risk.describe_rules", () => {
  it("calls GET /risk/rules and returns config", async () => {
    const captured: { url?: string; method?: string } = {};
    mockFetch(async (url, init) => {
      captured.url = url;
      captured.method = init?.method;
      return new Response(
        JSON.stringify({
          enabled: true,
          starting_balance: 10000.0,
          rules: [
            { name: "CooldownRule", short_desc: "单 symbol 冷却 5 分钟" },
            { name: "MaxDrawdownRule", short_desc: "账户回撤 > 15% 即停" },
          ],
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    });

    const result = await riskDescribeRulesTool.execute({} as never, ctx());
    expect(captured.url).toBe("http://paper-mock.test/risk/rules");
    expect(captured.method).toBe("GET");
    expect(result.enabled).toBe(true);
    expect(result.rules).toHaveLength(2);
    expect(result.rules[0].name).toBe("CooldownRule");
  });
});

// ────────────────────────────────────────────────────────────────────
// risk.list_locks
// ────────────────────────────────────────────────────────────────────

describe("risk.list_locks", () => {
  it("calls GET /risk/locks with no filter", async () => {
    const captured: { url?: string } = {};
    mockFetch(async (url) => {
      captured.url = url;
      return new Response(JSON.stringify({ locks: [] }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    });

    await riskListLocksTool.execute({} as never, ctx());
    expect(captured.url).toBe("http://paper-mock.test/risk/locks");
  });

  it("appends scope/market/symbol/limit as query params", async () => {
    const captured: { url?: string } = {};
    mockFetch(async (url) => {
      captured.url = url;
      return new Response(JSON.stringify({ locks: [] }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    });

    await riskListLocksTool.execute(
      {
        scope: "symbol",
        market: "binance",
        symbol: "BTC/USDT@binance",
        limit: 20,
      } as never,
      ctx(),
    );

    expect(captured.url).toContain("scope=symbol");
    expect(captured.url).toContain("market=binance");
    expect(captured.url).toContain("symbol=BTC%2FUSDT%40binance");
    expect(captured.url).toContain("limit=20");
  });

  it("returns lock list verbatim", async () => {
    mockFetch(async () =>
      new Response(
        JSON.stringify({
          locks: [
            {
              id: 1,
              scope: "symbol",
              market: "binance",
              symbol: "BTC/USDT@binance",
              side: "*",
              rule_name: "CooldownRule",
              reason: "冷却期 5 分钟",
              locked_at: "2026-05-26T12:00:00Z",
              locked_until: "2026-05-26T12:05:00Z",
            },
          ],
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );

    const result = await riskListLocksTool.execute({} as never, ctx());
    expect(result.locks).toHaveLength(1);
    expect(result.locks[0].rule_name).toBe("CooldownRule");
  });
});

// ────────────────────────────────────────────────────────────────────
// risk.unlock
// ────────────────────────────────────────────────────────────────────

describe("risk.unlock", () => {
  it("calls POST /risk/locks/{id}/unlock with reason", async () => {
    const captured: { url?: string; method?: string; body?: unknown } = {};
    mockFetch(async (url, init) => {
      captured.url = url;
      captured.method = init?.method;
      captured.body = JSON.parse(String(init?.body ?? "null"));
      return new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    });

    const result = await riskUnlockTool.execute(
      { lock_id: 42, reason: "管理员手动解除" } as never,
      ctx(),
    );

    expect(captured.url).toBe("http://paper-mock.test/risk/locks/42/unlock");
    expect(captured.method).toBe("POST");
    expect(captured.body).toEqual({ reason: "管理员手动解除" });
    expect(result.ok).toBe(true);
  });

  it("propagates 404 as HttpClientError", async () => {
    mockFetch(async () =>
      new Response(
        JSON.stringify({ code: "NOT_FOUND", message: "lock 999 not found" }),
        { status: 404, headers: { "Content-Type": "application/json" } },
      ),
    );

    await expect(
      riskUnlockTool.execute(
        { lock_id: 999, reason: "测试" } as never,
        ctx(),
      ),
    ).rejects.toThrow(/lock 999 not found|HTTP_404|NOT_FOUND/);
  });
});
