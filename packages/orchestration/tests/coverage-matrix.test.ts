import { describe, expect, it, vi, beforeEach } from "vitest";
const mocks = vi.hoisted(() => ({ list: vi.fn(), prepare: vi.fn(), resolveAsset: vi.fn() }));
vi.mock("../src/auth.js", () => ({ resolveRequestToken: async () => "owner-token", resolveRequestSubject: async () => "owner", mintServiceToken: async () => "scoped-service-token" }));
vi.mock("../src/config.js", () => ({ getSettings: () => ({ paperServiceUrl: "http://paper" }) }));
vi.mock("../src/clients/paper.js", () => ({ PaperClient: class { listStrategyRuns = mocks.list; } }));
vi.mock("../src/evolution/preparation.js", () => ({ prepareExperiment: mocks.prepare, EXPERIMENT_DATA_BLOCKERS: new Set(["EVOLUTION_DATA_STALE"]) }));
vi.mock("../src/clients/data.js", () => ({ DataClient: class { resolveAsset = mocks.resolveAsset; } }));
import { HttpClientError } from "../src/clients/http.js";
import { prepareCoverageMatrix, selectCoverageTarget } from "../src/evolution/coverage-matrix.js";

beforeEach(() => {
  vi.clearAllMocks();
  mocks.resolveAsset.mockImplementation(async ({ symbol }) => ({ event_asset_code: symbol.startsWith("BTC") ? "BTC" : "ETH" }));
});
describe("coverage-only selection", () => {
  it("excludes blocked rows even with high counts and uses deterministic asset ties", () => {
    expect(selectCoverageTarget([
      { asset: "SOL", target_id: "sol", status: "necessary_inputs_present", minimum_matched_event_pairs: 8, independent_event_upper_bound: 8 },
      { asset: "ETH", target_id: "eth", status: "blocked", minimum_matched_event_pairs: 8, independent_event_upper_bound: 90 },
      { asset: "BTC", target_id: "btc", status: "necessary_inputs_present", minimum_matched_event_pairs: 8, independent_event_upper_bound: 8 },
    ])).toBe("btc");
    expect(selectCoverageTarget([{ asset: "BTC", target_id: "btc", status: "necessary_inputs_present", minimum_matched_event_pairs: 8, independent_event_upper_bound: 7 }])).toBeNull();
  });
  it("uses one sampling time, oldest owned runner, and keeps per-asset failures blocked", async () => {
    mocks.list.mockResolvedValue([
      { id: "later", status: "running", symbol: "BTC/USDT", timeframe: "1h", started_at: "2026-10-02" },
      { id: "earlier", status: "running", venue: "coinbase", symbol: "BTC-USD", timeframe: "1h", started_at: "2026-10-01" },
      { id: "eth", status: "running", symbol: "ETH/USDT", timeframe: "4h", started_at: "2026-10-01" },
    ]);
    mocks.prepare.mockImplementation(async (input) => {
      if (input.targetId === "eth") throw new Error("private upstream content");
      return { status: "necessary_inputs_present", minimum_matched_event_pairs: 8, independent_event_upper_bound: 9 };
    });
    const result = await prepareCoverageMatrix({ authToken: "owner-token" });
    expect(result.recommended_target_id).toBeNull();
    expect(result.complete).toBe(false);
    expect(mocks.prepare.mock.calls.map(call => call[3])).toEqual([Date.parse(result.sampled_at), Date.parse(result.sampled_at)]);
    expect(result.rows[1].blocker_codes).toEqual(["EXPERIMENT_PREPARATION_UNAVAILABLE"]);
    expect(result.rows[2].blocker_codes).toEqual(["OWNED_RUNNER_UNAVAILABLE"]);
    expect(JSON.stringify(result)).not.toContain("private upstream");
    expect(result.execution_authorized).toBe(false);
  });
  it("excludes known bad inputs while retaining a fully checked eligible choice", async () => {
    mocks.list.mockResolvedValue([
      { id: "btc", status: "running", venue: "coinbase", symbol: "BTC-USD", timeframe: "1h", started_at: "2026-10-01" },
      { id: "eth", status: "running", symbol: "ETH/USDT", timeframe: "1h", started_at: "2026-10-01" },
    ]);
    mocks.prepare.mockImplementation(async input => {
      if (input.targetId === "eth") throw new HttpClientError("private", { status: 422, code: "EVOLUTION_DATA_STALE" });
      return { status: "necessary_inputs_present", independent_event_upper_bound: 9, minimum_matched_event_pairs: 8 };
    });
    const result = await prepareCoverageMatrix({ authToken: "owner-token" });
    expect(result.complete).toBe(true);
    expect(result.recommended_target_id).toBe("btc");
    expect(mocks.resolveAsset).toHaveBeenCalledWith({ venue: "coinbase", symbol: "BTC-USD" });
    expect(result.rows[1].status).toBe("blocked");
  });
  it("does not rank a potentially truncated runner inventory", async () => {
    mocks.list.mockResolvedValue(Array.from({ length: 200 }, () => ({})));
    expect(await prepareCoverageMatrix({ authToken: "owner-token" })).toMatchObject({ complete: false, recommended_target_id: null, rows: [{ blocker_codes: ["RUNNER_INVENTORY_INCOMPLETE"] }, { blocker_codes: ["RUNNER_INVENTORY_INCOMPLETE"] }, { blocker_codes: ["RUNNER_INVENTORY_INCOMPLETE"] }] });
    expect(mocks.list).toHaveBeenCalledWith({ status: "running", limit: 200 });
    expect(mocks.prepare).not.toHaveBeenCalled();
  });

  it("withholds ranking and preparation when an identity cannot be resolved", async () => {
    mocks.list.mockResolvedValue([{ id: "btc", venue: "binance", symbol: "BTC/USDT", status: "running", timeframe: "1h", started_at: "2026-10-01" }]);
    mocks.resolveAsset.mockRejectedValueOnce(new Error("private details"));
    const result = await prepareCoverageMatrix({ authToken: "owner-token" });
    expect(result).toMatchObject({ complete: false, recommended_target_id: null });
    expect(result.rows[0].blocker_codes).toEqual(["ASSET_IDENTITY_UNAVAILABLE"]);
    expect(mocks.prepare).not.toHaveBeenCalled();
    expect(JSON.stringify(result)).not.toContain("private details");
  });

});
