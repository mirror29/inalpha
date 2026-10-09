import { describe, expect, it } from "vitest";
import {
  evolutionMarket,
  evolutionOverview,
  independentExperiments,
  workflowState,
} from "./evolution-presentation";
import type {
  EvolutionCampaign,
  EvolutionLoop,
  EvolutionRunSummary,
} from "./types";

/** Check aggregation without treating a successful stage as a completed workflow. */
describe("evolution presentation", () => {
  it("keeps failures, aborted research and pending holdout distinct", () => {
    expect(workflowState("failed")).toBe("failed");
    expect(workflowState("aborted")).toBe("aborted");
    expect(workflowState("holdout_ready")).toBe("holdout_ready");
    expect(workflowState("graduated")).toBe("adoption_ready");
  });
  it("does not manufacture strategy names or stringify missing market fields", () => {
    expect(
      evolutionMarket({ symbol: "BTC/USDT", timeframe: "1h", venue: null }),
    ).toBe("BTC/USDT · 1h");
    expect(evolutionMarket({ symbol: { id: "secret" } })).toBe("");
  });
  it("hides stage duplicates while retaining independent historical research", () => {
    const loops = [
      { e1_run_id: "baseline", campaign_id: "search" },
    ] as EvolutionLoop[];
    const campaigns = [
      { campaign_id: "search", frozen_config: {} },
      { campaign_id: "standalone", frozen_config: {} },
    ] as EvolutionCampaign[];
    const runs = [
      { run_id: "baseline" },
      { run_id: "old-run" },
    ] as EvolutionRunSummary[];
    const history = independentExperiments(loops, campaigns, runs);
    expect(history.campaigns.map((item) => item.campaign_id)).toEqual([
      "standalone",
    ]);
    expect(history.runs.map((item) => item.run_id)).toEqual(["old-run"]);
  });
});

/** Aggregate research states without double-counting nested stage records. */
describe("evolutionOverview", () => {
  it("counts standalone E1 completed runs without marking them adoption ready", () => {
    const result = evolutionOverview([], [], [{ run_id: "done", status: "completed" }] as EvolutionRunSummary[]);
    expect(result).toMatchObject({ loadedTasks: 1, researching: 0, observing: 0, ready: 0 });
  });
  it("counts independent work and deduplicates attached E1 and campaigns", () => {
    const result = evolutionOverview(
      [{ e1_run_id: "nested", campaign_id: "nested-campaign", status: "waiting_forward" }] as EvolutionLoop[],
      [{ campaign_id: "nested-campaign", status: "waiting_forward", frozen_config: {} },
       { campaign_id: "independent", status: "replaying", frozen_config: {} }] as EvolutionCampaign[],
      [{ run_id: "nested", status: "completed" }, { run_id: "active", status: "running" }] as EvolutionRunSummary[],
    );
    expect(result).toMatchObject({ loadedTasks: 3, researching: 2, observing: 1, ready: 0 });
  });
});
