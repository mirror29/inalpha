import { describe, expect, it } from "vitest";
import type { EvolutionCandidateSummary, EvolutionRun } from "./types";
import { candidateCost, evolutionCosts, evolutionUsd } from "./evolution-cost";

const slot = (value: Partial<EvolutionCandidateSummary>) =>
  value as EvolutionCandidateSummary;
const run = (candidates: EvolutionCandidateSummary[], extra = {}) =>
  ({
    run_id: "run",
    candidates,
    llm_cost_usd: 0.03,
    known_cost_usd: 0.01,
    unknown_usage_count: 1,
    ...extra,
  }) as EvolutionRun;

describe("evolution receipt presentation", () => {
  it("preserves small nonzero estimates in display", () => {
    expect(evolutionUsd(0.0000003)).toBe("<$0.0001");
    expect(evolutionUsd(0)).toBe("$0.0000");
    expect(evolutionUsd(null)).toBeNull();
  });
  it("never converts missing or legacy zero costs into confirmed receipts", () => {
    expect(candidateCost(slot({ llm_cost_usd: null })).state).toBe("unknown");
    expect(candidateCost(slot({ llm_cost_usd: 0 })).confirmed).toBeNull();
    expect(
      candidateCost(slot({ llm_cost_usd: NaN, usage_status: "known" })).state,
    ).toBe("unknown");
    expect(
      candidateCost(slot({ llm_cost_usd: 0, usage_status: "known" })).confirmed,
    ).toBe(0);
    expect(candidateCost(slot({ usage_status: "not_called" })).state).toBe(
      "notCalled",
    );
  });
  it("separates repairs and retains failed costs without double counting totals", () => {
    const result = evolutionCosts(
      run([
        slot({
          outcome: "diff_failed",
          usage_status: "known",
          llm_cost_usd: 0.01,
        }),
        slot({
          outcome: "succeeded",
          parent_id: "failed",
          usage_status: "unknown",
          llm_cost_usd: null,
        }),
        slot({
          outcome: "cancelled",
          usage_status: "not_called",
          llm_cost_usd: null,
        }),
      ]),
    );
    expect(result.known).toBe(0.01);
    expect(result.recorded).toBe(0.03);
    expect(result.unknown).toBe(1);
    expect(result.phases[0]).toEqual({
      key: "generation",
      calls: 1,
      known: 0.01,
      unknown: 0,
    });
    expect(result.phases[1]).toEqual({
      key: "repair",
      calls: 1,
      known: 0,
      unknown: 1,
    });
    expect(result.rejectedKnown).toBe(0.01);
  });
  it("preserves legacy recorded amounts and keeps missing aggregate receipts unavailable", () => {
    const result = evolutionCosts(
      run([slot({ outcome: "mutation_failed", llm_cost_usd: 0.03 })], {
        known_cost_usd: undefined,
        unknown_usage_count: undefined,
      }),
    );
    expect(result.known).toBeNull();
    expect(result.recorded).toBe(0.03);
    expect(result.unknown).toBe(1);
    expect(result.rejectedUnknown).toBe(1);
  });
});
