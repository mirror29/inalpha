import { beforeEach, describe, expect, it, vi } from "vitest";
import { evolutionApprovalPreflight } from "../src/tools/evolution-preflight.js";
import { getEvolverClient } from "../src/tools/evolver-shared.js";
import { resolveEvolutionSeedContext } from "../src/tools/evolution-target.js";
import { buildEvolutionStartRequest, evolutionRequestDigest } from "../src/clients/evolver.js";
import { USER_LLM_SNAPSHOT_KEY } from "../src/mastra/llm/evolution-snapshot.js";

vi.mock("../src/tools/evolution-target.js", () => ({ resolveEvolutionSeedContext: vi.fn() }));
vi.mock("../src/tools/evolver-shared.js", async (importOriginal) => ({ ...await importOriginal<object>(), getEvolverClient: vi.fn() }));

const snapshot = { config_id: "owned-model", provider: "deepseek", model: "deepseek-flash", base_url: "https://api.deepseek.com",
  config_digest: "a".repeat(64), pricing: { version: "test", currency: "USD", input_usd_per_million: 0.3, output_usd_per_million: 1.2,
    assumed_input_tokens: 24000, max_output_tokens: 8192, estimated_max_usd_per_candidate: 0.02 } } as const;
const config = { venue: "binance", symbol: "BTC/USDT", timeframe: "1h", from_ts: "2026-01-01T00:00:00Z", as_of: "2026-07-01T00:00:00Z",
  initial_cash: 10000, fee_rate: 0.001, validation_split: 0.3, params: {}, funding_rate: 0, trading_mode: "spot", leverage: 1 } as const;
const preflightRun = vi.fn(async (request) => ({ seed_strategy_id: request.seed_strategy_id, seed_source_hash: "b".repeat(64),
  request_digest: evolutionRequestDigest({ ...request, preparation: { seed_source_hash: "b".repeat(64), dataset_content_sha256: "c".repeat(64) } }),
  dataset_manifest: { content_sha256: "c".repeat(64) }, estimated_max_cost_usd: 0.08 }));
const requestContext = new Map<string, unknown>([[USER_LLM_SNAPSHOT_KEY, snapshot]]);

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(getEvolverClient).mockResolvedValue({ preflightRun } as never);
});

describe("candidate preparation without model-assembled context", () => {
  it("fills omitted config and canonical seed from owned authoritative context", async () => {
    vi.mocked(resolveEvolutionSeedContext).mockResolvedValue({ reference: "candidate:owned", config, evidence: { seed_label: "Trend seed" } });
    const prepared = await evolutionApprovalPreflight("evolver.run_evolution", { seedStrategyId: "bare-owned-id", budget: 4 }, { requestContext });
    expect(prepared?.input).toMatchObject({ seedStrategyId: "candidate:owned", config });
    expect(prepared?.summary).toMatchObject({ seed_context: { seed_label: "Trend seed" } });
    expect(preflightRun).toHaveBeenCalledWith(buildEvolutionStartRequest({ seedStrategyId: "candidate:owned", budget: 4, config, llmSnapshot: snapshot }));
  });
  it("rejects blocked context even when a model supplies a complete replacement window", async () => {
    vi.mocked(resolveEvolutionSeedContext).mockRejectedValue(new Error("EVOLUTION_SEED_CONTEXT_REQUIRED"));
    await expect(evolutionApprovalPreflight("evolver.run_evolution", { seedStrategyId: "candidate:owned", config }, { requestContext })).rejects.toThrow("EVOLUTION_SEED_CONTEXT_REQUIRED");
    expect(preflightRun).not.toHaveBeenCalled();
  });
  it("requires explicit config for built-in seeds and preserves frozen retry input", async () => {
    vi.mocked(resolveEvolutionSeedContext).mockResolvedValue(null);
    await expect(evolutionApprovalPreflight("evolver.run_evolution", { seedStrategyId: "sma_cross_v1" }, { requestContext })).rejects.toThrow("EVOLUTION_SEED_CONTEXT_REQUIRED");
    vi.mocked(resolveEvolutionSeedContext).mockClear();
    const retry = await evolutionApprovalPreflight("evolver.run_evolution", { seedStrategyId: "candidate:original", config,
      retryOfRunId: "11111111-1111-4111-8111-111111111111" }, { requestContext });
    expect(retry?.input.config).toEqual(config);
    expect(resolveEvolutionSeedContext).not.toHaveBeenCalled();
  });
});
