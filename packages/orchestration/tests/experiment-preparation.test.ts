import { describe, expect, it, vi, beforeEach } from "vitest";
const mocks = vi.hoisted(() => ({ resolve: vi.fn(), freeze: vi.fn(), prepare: vi.fn(), verify: vi.fn() }));
vi.mock("../src/tools/evolution-target.js", () => ({ resolveEvolutionTarget: mocks.resolve }));
vi.mock("../src/tools/evolver.js", () => ({ createAutomaticEventSnapshot: mocks.freeze }));
vi.mock("../src/tools/evolver-shared.js", () => ({ getEvolverClient: async () => ({ prepareExperiment: mocks.prepare }) }));
vi.mock("../src/auth.js", () => ({ verifyToken: mocks.verify }));
import { Hono } from "hono";
import { prepareExperiment, suggestExperimentWindow, prepareExperimentHandler } from "../src/evolution/preparation.js";
const input = { targetKind: "strategy_candidate" as const, targetId: "22222222-2222-4222-8222-222222222222" };

beforeEach(() => {
  vi.clearAllMocks();
  mocks.resolve.mockResolvedValue({ next_action: "start_loop", start_input: { seedStrategyId: "candidate:owned", config: {
    venue: "binance", symbol: "SOL/USDT", timeframe: "1h", from_ts: "2025-01-01T00:00:00Z", as_of: "2026-01-01T00:00:00Z",
  } } });
  mocks.freeze.mockResolvedValue({ snapshotId: "snapshot", asset: { event_asset_code: "SOL" }, facts: [{ available_at: "2026-09-30T03:12:00Z" }] });
  mocks.prepare.mockResolvedValue({ status: "blocked", independent_event_upper_bound: 0 });
});

describe("explicit new experiment preparation", () => {
  it("uses first availability and closed-bar boundary, never publication or returns", () => {
    expect(suggestExperimentWindow([{ available_at: "2026-09-30T03:12:00Z", published_at: "2020-01-01", return: 900 }], "1h", Date.parse("2026-10-10T05:42:00Z"))).toEqual({ from_ts: "2026-09-30T03:00:00.000Z", as_of: "2026-10-10T05:00:00.000Z" });
    expect(suggestExperimentWindow([], "1h", Date.now())).toBeNull();
  });
  it("does not reinterpret an existing experiment as a new window", async () => {
    mocks.resolve.mockResolvedValue({ next_action: "inspect_loop", evidence: { loop_id: "existing" } });
    expect(await prepareExperiment(input, { authToken: "owner" })).toMatchObject({ status: "existing_experiment" });
    expect(mocks.freeze).not.toHaveBeenCalled();
  });
  it("rejects an unowned seed before accessing data", async () => {
    mocks.resolve.mockRejectedValue(new Error("not found"));
    await expect(prepareExperiment(input, { authToken: "owner" })).rejects.toThrow("not found");
    expect(mocks.freeze).not.toHaveBeenCalled();
  });
  it("preserves explicit dates even when coverage is insufficient", async () => {
    const window = { from_ts: "2026-09-30T04:00:00Z", as_of: "2026-10-01T05:00:00Z" };
    const result = await prepareExperiment({ ...input, window }, { authToken: "owner" });
    expect(result).toMatchObject({ status: "blocked", config: window, window_source: "explicit", model_calls: 0, execution_authorized: false });
    expect(mocks.prepare.mock.calls[0]?.[0].config).toMatchObject(window);
  });
  it("blocks empty evidence without loading bars or authorizing execution", async () => {
    mocks.freeze.mockResolvedValue({ snapshotId: "empty", asset: {}, facts: [] });
    expect(await prepareExperiment(input, { authToken: "owner" })).toMatchObject({ status: "blocked", blocker_codes: ["EVENT_SNAPSHOT_EMPTY"] });
    expect(mocks.prepare).not.toHaveBeenCalled();
  });
});

/** Authentication must fail before target resolution or any Data call. */
describe("preparation HTTP boundary", () => {
  const app = new Hono().post("/prepare", prepareExperimentHandler);
  it("rejects missing identity and service-scoped tokens", async () => {
    expect((await app.request("/prepare", { method: "POST" })).status).toBe(401);
    mocks.verify.mockResolvedValue({ sub: "owner", token_use: "service" });
    expect((await app.request("/prepare", { method: "POST", headers: { Authorization: "Bearer test" } })).status).toBe(401);
    expect(mocks.resolve).not.toHaveBeenCalled();
  });
  it("rejects a reversed experiment window before accessing data", async () => {
    mocks.verify.mockResolvedValue({ sub: "owner" });
    const response = await app.request("/prepare", {
      method: "POST", headers: { Authorization: "Bearer test", "Content-Type": "application/json" },
      body: JSON.stringify({ ...input, window: { from_ts: "2026-10-02T00:00:00Z", as_of: "2026-10-01T00:00:00Z" } }),
    });
    expect(response.status).toBe(400);
    expect(await response.json()).toEqual({ error: "EXPERIMENT_WINDOW_INVALID" });
    expect(mocks.resolve).not.toHaveBeenCalled();
  });
});
