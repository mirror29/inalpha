/**
 * ADR-0053 阶段 A · data.get_fundamentals 的 asOf（point-in-time）透传单测。
 *
 * 覆盖：asOf 传入 → GET /fundamentals?as_of=...；不传 → 不带 as_of。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { clearSettings, setSettings } from "../src/config.js";
import { dataGetFundamentalsTool } from "../src/tools/index.js";

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

const FIN_RESPONSE = {
  venue: "akshare",
  symbol: "sh.600519",
  available: false,
  reason: "no financials published as of 2020-01-01T00:00:00.000Z",
};

describe("data.get_fundamentals · asOf PIT 透传", () => {
  it("传 asOf → GET /fundamentals 带 as_of 查询参数", async () => {
    let capturedUrl = "";
    mockFetch(async (url) => {
      capturedUrl = url;
      return new Response(JSON.stringify(FIN_RESPONSE), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    });

    await dataGetFundamentalsTool.execute!(
      {
        venue: "akshare",
        symbol: "sh.600519",
        asOf: "2020-01-01T00:00:00Z",
      } as never,
      ctx(),
    );

    expect(capturedUrl).toContain("/fundamentals");
    expect(capturedUrl).toContain("as_of=");
    expect(decodeURIComponent(capturedUrl)).toContain("2020-01-01T00:00:00Z");
  });

  it("schema 接受 +00:00 与 Z 两种偏移格式（akshare/Python isoformat 回显，#102 CR）", () => {
    // agent 把上一轮响应里的 as_of(+00:00)复制回来——schema 必须收，否则 round-trip 失败
    const schema = dataGetFundamentalsTool.inputSchema!;
    for (const asOf of ["2020-01-01T00:00:00+00:00", "2020-01-01T00:00:00Z"]) {
      const r = schema.safeParse({ venue: "akshare", symbol: "sh.600519", asOf });
      expect(r.success).toBe(true);
    }
  });

  it("不传 asOf → URL 不带 as_of（研究当下不做 PIT 截断）", async () => {
    let capturedUrl = "";
    mockFetch(async (url) => {
      capturedUrl = url;
      return new Response(JSON.stringify({ ...FIN_RESPONSE, available: true }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    });

    await dataGetFundamentalsTool.execute!(
      { venue: "akshare", symbol: "sh.600519" } as never,
      ctx(),
    );

    expect(capturedUrl).toContain("/fundamentals");
    expect(capturedUrl).not.toContain("as_of");
  });
});
