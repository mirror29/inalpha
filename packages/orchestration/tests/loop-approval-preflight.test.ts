import { beforeEach, expect, it, vi } from "vitest";
const mocks = vi.hoisted(() => ({ prepare: vi.fn() }));
vi.mock("../src/evolution/preparation.js", () => ({ prepareExperiment: mocks.prepare }));
import { evolutionApprovalPreflight } from "../src/tools/evolution-preflight.js";
import { buildEvolutionLLMSnapshot, USER_LLM_SNAPSHOT_KEY } from "../src/mastra/llm/evolution-snapshot.js";

const model = buildEvolutionLLMSnapshot({ id: "owned", provider: "deepseek", model: "deepseek-flash", api_key: "test-not-forwarded" });
const ctx = { requestContext: new Map([[USER_LLM_SNAPSHOT_KEY, model]]) };
const input = { targetKind: "paper_runner", targetId: "22222222-2222-4222-8222-222222222222", budget: 4 };
const config = { venue: "binance", symbol: "SOL/USDT", timeframe: "1h", from_ts: "2026-09-30T00:00:00Z", as_of: "2026-10-10T00:00:00Z" };
beforeEach(() => mocks.prepare.mockReset());

it("does not create a cost approval for insufficient inputs", async () => {
  mocks.prepare.mockResolvedValue({ status: "blocked" });
  await expect(evolutionApprovalPreflight("evolver.start_evolution_loop", input, ctx)).rejects.toThrow("E2_INPUT_COVERAGE_INSUFFICIENT");
});

it("freezes server-derived bars, source, dates and model-dependent budget without model calls", async () => {
  mocks.prepare.mockResolvedValue({ status: "necessary_inputs_present", config, event_snapshot_id: "11111111-1111-4111-8111-111111111111", seed_source_hash: "a".repeat(64), dataset_content_sha256: "b".repeat(64) });
  const result = await evolutionApprovalPreflight("evolver.start_evolution_loop", input, ctx);
  expect(result?.input).toMatchObject({ ...input, experiment: { config, seed_source_hash: "a".repeat(64), dataset_content_sha256: "b".repeat(64) } });
  expect(JSON.stringify(result)).not.toContain("test-not-forwarded");
  expect(result?.summary).toMatchObject({ cost_scope: "evolution_generation_only" });
  await expect(evolutionApprovalPreflight("evolver.start_evolution_loop", { ...input, maxCostUsd: 0.000001 }, ctx)).rejects.toThrow("LOOP_BUDGET_INSUFFICIENT");
});

it("surfaces quality and provenance blockers without creating approval", async () => {
  mocks.prepare.mockResolvedValue({ status: "blocked", blocker_codes: ["E2_EVENT_QUALITY_REVIEW_REQUIRED", "E2_EVENT_PROVENANCE_UNVERIFIED"] });
  await expect(evolutionApprovalPreflight("evolver.start_evolution_loop", input, ctx)).rejects.toThrow("E2_EVENT_QUALITY_REVIEW_REQUIRED, E2_EVENT_PROVENANCE_UNVERIFIED");
});

/** Machine-readable blockers remain separate from the human message. */
it("returns a stable code with every structured blocker", async () => {
  const blocker_codes = ["E2_INPUT_COVERAGE_INSUFFICIENT", "E2_EVENT_QUALITY_REVIEW_REQUIRED"];
  mocks.prepare.mockResolvedValue({ status: "blocked", blocker_codes });
  await expect(evolutionApprovalPreflight("evolver.start_evolution_loop", input, ctx)).rejects.toMatchObject({ code: "E2_INPUT_BLOCKED", blocker_codes });
});
