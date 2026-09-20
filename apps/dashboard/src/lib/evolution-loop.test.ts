import { describe, expect, it } from "vitest";

import { loopRefreshInterval, loopStage } from "./evolution-loop";

describe("durable loop progress", () => {
  it("keeps pre-campaign research visible and polls without requiring another user action", () => {
    expect(loopStage("target_resolved")).toBe(0);
    expect(loopStage("baseline_ready")).toBe(1);
    expect(loopRefreshInterval("baseline_ready")).toBe(15_000);
    expect(loopRefreshInterval("waiting_forward")).toBe(60_000);
  });

  it("does not imply a winner when evidence is insufficient or execution fails", () => {
    expect(loopStage("insufficient_evidence")).toBeNull();
    expect(loopStage("failed")).toBeNull();
    expect(loopRefreshInterval("insufficient_evidence")).toBe(0);
    expect(loopStage("adoption_ready")).toBe(4);
  });
});
