import type { EvolutionHypothesis } from "./types";

/** Resolves only prior-generation parents within the current owned campaign payload. */
export function hypothesisParents(
  hypothesis: EvolutionHypothesis,
  hypotheses: EvolutionHypothesis[],
): EvolutionHypothesis[] {
  const wanted = new Set(hypothesis.parent_ids ?? []);
  return hypotheses.filter(
    (parent) =>
      wanted.has(parent.hypothesis_id) &&
      parent.campaign_id === hypothesis.campaign_id &&
      parent.generation < hypothesis.generation,
  );
}
