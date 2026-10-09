import { describe, expect, it } from "vitest";
import type { EvolutionCandidateSummary, EvolutionRun } from "./types";
import { evolutionComparison, evaluationNumber } from "./evolution-comparison";

const report = (fitness: number, extra = {}) => ({
  schema_version: "e1.report.v1",
  fitness,
  period_start: "2025-01-01T00:00:00Z",
  period_end: "2025-02-01T00:00:00Z",
  num_bars: 744,
  initial_cash: 10_000,
  annualization_periods: 8760,
  total_return_pct: 4,
  ...extra,
});
const candidate = (slot: number, fitness: number, extra = {}) =>
  ({
    candidate_id: `candidate-${slot}`,
    run_id: "run",
    generation: 1,
    slot,
    fitness,
    outcome: "succeeded",
    stage: "completed",
    source_code: null,
    source_hash: null,
    unified_diff: null,
    mutation_hint: null,
    llm_cost_usd: 0.01,
    audit_snapshot: null,
    contract_snapshot: null,
    error_code: null,
    error_message: null,
    overfitting_risk: "high",
    created_at: null,
    updated_at: null,
    evaluation_snapshot: report(fitness),
    ...extra,
  }) as EvolutionCandidateSummary;
const run = (extra = {}) =>
  ({
    run_id: "run",
    seed_strategy_id: "seed",
    budget: 4,
    llm_snapshot: null,
    llm_config_digest: null,
    status: "completed",
    active_stage: "completed",
    config: { trading_mode: "spot" },
    llm_cost_usd: 0.02,
    queued_at: "2026-10-09T00:00:00Z",
    started_at: null,
    finished_at: null,
    dataset_manifest: null,
    failure_code: null,
    failure_message: null,
    attempted: 2,
    succeeded: 2,
    rejected: 0,
    seed_report_snapshot: report(0.5),
    baseline_snapshot: report(0.3),
    candidates: [candidate(0, 0.4), candidate(1, 0.8)],
    ...extra,
  }) as EvolutionRun;

describe("same-window evolution comparison", () => {
  it("selects the best passed candidate and distinguishes improvement", () => {
    const result = evolutionComparison(run());
    expect(result.best?.slot).toBe(1);
    expect(result.improvement).toBeCloseTo(0.3);
    expect(result.verdict).toBe("improved");
    expect(result.benchmarkComparable).toBe(true);
  });
  it("passing evaluation does not imply improvement", () => {
    expect(
      evolutionComparison(run({ candidates: [candidate(0, 0.4)] })).verdict,
    ).toBe("notImproved");
    expect(evolutionComparison(run({ candidates: [] })).verdict).toBe(
      "noPassedCandidate",
    );
    expect(evolutionComparison(run({ status: "running" })).verdict).toBe(
      "unfinished",
    );
  });
  it("does not compare missing or mismatched windows and capital", () => {
    for (const change of [
      { period_end: "different" },
      { initial_cash: 100 },
      { num_bars: null },
      { period_start: "" },
      { annualization_periods: 0 },
    ]) {
      const result = evolutionComparison(
        run({ seed_report_snapshot: report(0.5, change) }),
      );
      expect(result.verdict).toBe("comparisonUnavailable");
      expect(result.improvement).toBeNull();
    }
  });
  it("does not assert improvement when ranking and stored report disagree", () => {
    const result = evolutionComparison(
      run({
        candidates: [candidate(0, 99, { evaluation_snapshot: report(0.2) })],
      }),
    );
    expect(result.verdict).toBe("comparisonUnavailable");
  });
  it("does not mix legacy train-only metrics with a full-window baseline", () => {
    const result = evolutionComparison(
      run({ seed_report_snapshot: { validation: { train: { fitness: 99 } } } }),
    );
    expect(result.seed).toBeNull();
    expect(result.verdict).toBe("comparisonUnavailable");
  });
  it("labels a spot benchmark as a different execution basis for perpetuals", () => {
    expect(
      evolutionComparison(
        run({ config: { trading_mode: "perp", leverage: 3 } }),
      ).benchmarkComparable,
    ).toBe(false);
  });
  it("ignores rejected candidates and non-finite fitness", () => {
    const result = evolutionComparison(
      run({
        candidates: [
          candidate(0, 999, { outcome: "ast_rejected" }),
          candidate(1, NaN),
          candidate(2, 0),
        ],
      }),
    );
    expect(result.best?.slot).toBe(2);
    expect(result.verdict).toBe("notImproved");
    expect(evaluationNumber({ fitness: NaN }, "fitness")).toBeNull();
    expect(evaluationNumber({}, "num_trades")).toBeNull();
    expect(evaluationNumber({ num_trades: 0 }, "num_trades")).toBe(0);
  });
});
