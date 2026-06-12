/**
 * data.get_market_* tool 单测（D-12+ 行情归因）—— fetch mock，模式同 tools.test.ts。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { clearSettings, setSettings } from "../src/config.js";
import {
  dataGetMarketMoneyflowTool,
  dataGetMarketMoversTool,
  dataGetMarketNewsTool,
  dataGetMarketSectorsTool,
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

describe("data.get_market_news", () => {
  it("calls /market/news with market+limit and forwards token", async () => {
    let capturedUrl = "";
    let capturedAuth = "";
    mockFetch(async (url, init) => {
      capturedUrl = String(url);
      capturedAuth = String((init?.headers as Record<string, string>)?.Authorization ?? "");
      return new Response(
        JSON.stringify({ market: "cn", items: [{ title: "快讯" }] }),
        { status: 200, headers: { "content-type": "application/json" } },
      );
    });
    const out = (await dataGetMarketNewsTool.execute!(
      { market: "cn", limit: 5 },
      ctx(),
    )) as Record<string, unknown>;
    expect(capturedUrl).toContain("/market/news");
    expect(capturedUrl).toContain("market=cn");
    expect(capturedUrl).toContain("limit=5");
    expect(capturedAuth).toBe(`Bearer ${TEST_TOKEN}`);
    expect((out.items as unknown[]).length).toBe(1);
  });

  it("upstream 502 returns error field instead of throwing", async () => {
    mockFetch(async () =>
      new Response(
        JSON.stringify({ code: "MARKET_DATA_UNAVAILABLE", message: "blocked" }),
        { status: 502, headers: { "content-type": "application/json" } },
      ),
    );
    const out = (await dataGetMarketNewsTool.execute!(
      { market: "cn", limit: 20 },
      ctx(),
    )) as Record<string, unknown>;
    expect(out.items).toEqual([]);
    expect(String(out.error)).toContain("502");
  });
});

describe("data.get_market_sectors", () => {
  it("calls /market/sectors with top_n", async () => {
    let capturedUrl = "";
    mockFetch(async (url) => {
      capturedUrl = String(url);
      return new Response(
        JSON.stringify({ market: "cn", total_boards: 496, top: [], bottom: [] }),
        { status: 200, headers: { "content-type": "application/json" } },
      );
    });
    const out = (await dataGetMarketSectorsTool.execute!(
      { market: "cn", topN: 3 },
      ctx(),
    )) as Record<string, unknown>;
    expect(capturedUrl).toContain("/market/sectors");
    expect(capturedUrl).toContain("top_n=3");
    expect(out.total_boards).toBe(496);
  });
});

describe("data.get_market_moneyflow", () => {
  it("calls /market/moneyflow and passes through note", async () => {
    mockFetch(async () =>
      new Response(
        JSON.stringify({ market: "cn", north_net_yi_cny: -40.4, note: "估算口径" }),
        { status: 200, headers: { "content-type": "application/json" } },
      ),
    );
    const out = (await dataGetMarketMoneyflowTool.execute!(
      { market: "cn" },
      ctx(),
    )) as Record<string, unknown>;
    expect(out.north_net_yi_cny).toBe(-40.4);
    expect(out.note).toBe("估算口径");
  });
});

describe("data.get_market_movers", () => {
  it("calls /market/movers with limit", async () => {
    let capturedUrl = "";
    mockFetch(async (url) => {
      capturedUrl = String(url);
      return new Response(
        JSON.stringify({ market: "cn", items: [{ code: "688163", tags: ["题材"] }] }),
        { status: 200, headers: { "content-type": "application/json" } },
      );
    });
    const out = (await dataGetMarketMoversTool.execute!(
      { market: "cn", limit: 10 },
      ctx(),
    )) as Record<string, unknown>;
    expect(capturedUrl).toContain("/market/movers");
    expect(capturedUrl).toContain("limit=10");
    expect((out.items as unknown[]).length).toBe(1);
  });
});

describe("tool ids", () => {
  it("market tool ids follow data.<verb> convention", () => {
    expect(dataGetMarketNewsTool.id).toBe("data.get_market_news");
    expect(dataGetMarketSectorsTool.id).toBe("data.get_market_sectors");
    expect(dataGetMarketMoneyflowTool.id).toBe("data.get_market_moneyflow");
    expect(dataGetMarketMoversTool.id).toBe("data.get_market_movers");
  });
});
