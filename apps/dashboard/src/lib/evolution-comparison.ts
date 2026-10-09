import type { EvolutionCandidateSummary, EvolutionRun } from "./types";

type Report = Record<string, unknown>;

/** Only complete E1 report snapshots describe the full evaluated window. */
export function fullEvaluationReport(snapshot: Report | null): Report | null {
  return snapshot?.schema_version === "e1.report.v1" ? snapshot : null;
}

/** Missing or non-finite metrics remain unavailable rather than becoming zero. */
export function evaluationNumber(
  report: Report | null,
  key: string,
): number | null {
  const value = report?.[key];
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

/** Match report boundaries and capital before showing improvement against another report. */
export function matchingEvaluationScope(
  left: Report | null,
  right: Report | null,
): boolean {
  if (!left || !right) return false;
  for (const report of [left, right]) {
    const start =
      typeof report.period_start === "string"
        ? Date.parse(report.period_start)
        : NaN;
    const end =
      typeof report.period_end === "string"
        ? Date.parse(report.period_end)
        : NaN;
    if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start)
      return false;
    if (
      ["num_bars", "initial_cash", "annualization_periods"].some(
        (key) => (evaluationNumber(report, key) ?? 0) <= 0,
      )
    )
      return false;
  }
  return [
    "period_start",
    "period_end",
    "num_bars",
    "initial_cash",
    "annualization_periods",
  ].every(
    (key) =>
      left[key] !== null && left[key] !== undefined && left[key] === right[key],
  );
}

/** Compare same-run full-window results; completion and improvement do not imply adoption. */
export function evolutionComparison(run: EvolutionRun) {
  const seed = fullEvaluationReport(run.seed_report_snapshot);
  const benchmark = fullEvaluationReport(run.baseline_snapshot);
  const successful = run.candidates.filter(
    (candidate) => candidate.outcome === "succeeded",
  );
  const ranked = successful
    .filter(
      (candidate) =>
        typeof candidate.fitness === "number" &&
        Number.isFinite(candidate.fitness),
    )
    .sort(
      (left, right) => right.fitness! - left.fitness! || left.slot - right.slot,
    );
  const best: EvolutionCandidateSummary | null = ranked[0] ?? null;
  const bestReport = fullEvaluationReport(best?.evaluation_snapshot ?? null);
  const candidateComparable =
    matchingEvaluationScope(seed, bestReport) &&
    evaluationNumber(bestReport, "fitness") === best?.fitness;
  // Buy-and-hold uses spot execution. A perpetual strategy's funding/leverage differ.
  const benchmarkComparable =
    (run.config.trading_mode ?? "spot") === "spot" &&
    matchingEvaluationScope(seed, benchmark);
  const seedFitness = evaluationNumber(seed, "fitness");
  const bestFitness = evaluationNumber(bestReport, "fitness");
  const improvement =
    candidateComparable && seedFitness !== null && bestFitness !== null
      ? bestFitness - seedFitness
      : null;
  return {
    seed,
    benchmark,
    best,
    bestReport,
    successfulCount: successful.length,
    candidateComparable,
    benchmarkComparable,
    improvement,
    verdict:
      run.status !== "completed"
        ? "unfinished"
        : successful.length === 0
          ? "noPassedCandidate"
          : improvement === null
            ? "comparisonUnavailable"
            : improvement > 0
              ? "improved"
              : "notImproved",
  };
}
