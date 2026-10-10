import { beforeEach, expect, it, vi } from "vitest";
import { AUTH_SUB_KEY } from "../src/hooks/with-hooks.js";
import { APPROVAL_OPERATION_ID_KEY, buildEvolutionLLMSnapshot, USER_LLM_SNAPSHOT_KEY } from "../src/mastra/llm/evolution-snapshot.js";

const mocks = vi.hoisted(() => ({
  preflight: vi.fn(), start: vi.fn(), mint: vi.fn(), resolve: vi.fn(),
}));
vi.mock("../src/tools/evolution-target.js", () => ({ resolveEvolutionTarget: mocks.resolve }));
vi.mock("../src/tools/evolver.js", () => ({
  createAutomaticEventSnapshot: vi.fn(async () => ({ snapshotId: "11111111-1111-4111-8111-111111111111", asset: { asset_id: "asset:BTC", event_asset_code: "BTC" } })),
}));
vi.mock("../src/mastra/llm/evolution-credential-grant.js", () => ({ mintEvolutionCredentialGrant: mocks.mint }));
vi.mock("../src/tools/evolver-shared.js", async (original) => ({
  ...await original<typeof import("../src/tools/evolver-shared.js")>(),
  getEvolverClient: async () => ({
    getEventEvolutionCapabilities: async () => ({ durable_loop_enabled: true }),
    preflightEvolutionLoop: mocks.preflight, startEvolutionLoop: mocks.start,
  }),
}));
import { evolverStartLoopTool } from "../src/tools/evolution-loop.js";

const snapshot = buildEvolutionLLMSnapshot({ id: "config-1", provider: "deepseek", model: "deepseek-flash", api_key: "not-forwarded" });
const context = { requestContext: new Map<string, unknown>([
  [AUTH_SUB_KEY, "owner"], [APPROVAL_OPERATION_ID_KEY, "operation-1"], [USER_LLM_SNAPSHOT_KEY, snapshot],
]) } as never;
const input = { targetKind: "strategy_candidate" as const, targetId: "22222222-2222-4222-8222-222222222222", budget: 1 };

beforeEach(() => {
  vi.clearAllMocks();
  mocks.resolve.mockResolvedValue({ next_action: "start_loop", start_input: {
    seedStrategyId: `candidate:${input.targetId}`,
    config: { venue: "binance", symbol: "BTCUSDT", timeframe: "1h", from_ts: "2026-01-01T00:00:00Z", as_of: "2026-02-01T00:00:00Z" },
  } });
});

it("returns coverage blockers without signing or submitting paid execution", async () => {
  mocks.preflight.mockResolvedValue({ status: "blocked", selection_fact_count: 0 });
  const result = await evolverStartLoopTool.execute!(input, context);
  expect(result).toMatchObject({ status: "blocked", code: "E2_INPUT_COVERAGE_INSUFFICIENT", preflight: { selection_fact_count: 0 } });
  expect(mocks.mint).not.toHaveBeenCalled();
  expect(mocks.start).not.toHaveBeenCalled();
});

it("fails closed when the preparation service cannot answer", async () => {
  mocks.preflight.mockRejectedValue(new Error("unavailable"));
  await expect(evolverStartLoopTool.execute!(input, context)).rejects.toThrow("unavailable");
  expect(mocks.mint).not.toHaveBeenCalled();
  expect(mocks.start).not.toHaveBeenCalled();
});

it("signs and starts once after necessary inputs are present", async () => {
  mocks.preflight.mockResolvedValue({ status: "necessary_inputs_present" });
  mocks.mint.mockResolvedValue("bound-grant");
  mocks.start.mockResolvedValue({ loop_id: "created" });
  await expect(evolverStartLoopTool.execute!(input, context)).resolves.toEqual({ loop_id: "created" });
  expect(mocks.mint).toHaveBeenCalledOnce();
  expect(mocks.start).toHaveBeenCalledOnce();
});


it("rejects an unknown preflight status without minting authority", async () => {
  mocks.preflight.mockResolvedValue({ status: "unknown" });
  await expect(evolverStartLoopTool.execute!(input, context)).rejects.toThrow("E2_INPUT_PREFLIGHT_INVALID");
  expect(mocks.mint).not.toHaveBeenCalled();
});
