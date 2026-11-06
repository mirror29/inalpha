import { expect, it } from "vitest";
import { hypothesisParents } from "./evolution-hypothesis-lineage";
import type { EvolutionHypothesis } from "./types";

it("links only recorded earlier parents in the same campaign without guessing missing IDs", () => {
  const child = {
    hypothesis_id: "child",
    campaign_id: "owned",
    generation: 3,
    parent_ids: ["parent", "foreign", "future", "missing", "parent"],
  } as EvolutionHypothesis;
  const parents = [
    { hypothesis_id: "parent", campaign_id: "owned", generation: 2 },
    { hypothesis_id: "foreign", campaign_id: "other", generation: 2 },
    { hypothesis_id: "future", campaign_id: "owned", generation: 4 },
  ] as EvolutionHypothesis[];
  expect(hypothesisParents(child, parents)).toEqual([parents[0]]);
  expect(hypothesisParents({ ...child, parent_ids: [] }, parents)).toEqual([]);
});
