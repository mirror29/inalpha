import type { EvolutionCandidateSummary, EvolutionRun } from "./types";

/** Reject unavailable and non-finite amounts without fabricating a zero receipt. */
function amount(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) && value >= 0
    ? value
    : null;
}

/** Legacy recorded amounts remain visible but do not establish complete usage. */
export function candidateCost(candidate: EvolutionCandidateSummary) {
  const recorded = amount(candidate.llm_cost_usd);
  const state =
    candidate.usage_status === "not_called"
      ? "notCalled"
      : candidate.usage_status === "known" && recorded !== null
        ? "known"
        : "unknown";
  return { state, recorded, confirmed: state === "known" ? recorded : null };
}

/** Attribute candidate costs to generation or linked repair, retaining rejected costs. */
export function evolutionCosts(run: EvolutionRun) {
  const groups = ["generation", "repair"] as const;
  const phases = groups.map((key) => {
    const candidates = run.candidates.filter(
      (candidate) => Boolean(candidate.parent_id) === (key === "repair"),
    );
    const costs = candidates.map(candidateCost);
    return {
      key,
      calls: costs.filter((cost) => cost.state !== "notCalled").length,
      known: costs.reduce((total, cost) => total + (cost.confirmed ?? 0), 0),
      unknown: costs.filter((cost) => cost.state === "unknown").length,
    };
  });
  const rejected = run.candidates.filter(
    (candidate) => !["pending", "succeeded"].includes(candidate.outcome),
  );
  return {
    known: amount(run.known_cost_usd),
    recorded: amount(run.llm_cost_usd),
    unknown: Math.max(
      run.unknown_usage_count ?? 0,
      phases.reduce((total, phase) => total + phase.unknown, 0),
    ),
    phases,
    rejectedKnown: rejected.reduce(
      (total, candidate) => total + (candidateCost(candidate).confirmed ?? 0),
      0,
    ),
    rejectedUnknown: rejected.filter(
      (candidate) => candidateCost(candidate).state === "unknown",
    ).length,
  };
}

/** Small nonzero model estimates must not round down to a displayed zero. */
export function evolutionUsd(value: number | null): string | null {
  if (value === null) return null;
  return value > 0 && value < 0.0001 ? "<$0.0001" : `$${value.toFixed(4)}`;
}
