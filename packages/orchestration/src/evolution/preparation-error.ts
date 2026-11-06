const INPUT_BLOCKER_CODES = new Set([
  "E2_INPUT_COVERAGE_INSUFFICIENT", "E2_EVENT_QUALITY_REVIEW_REQUIRED",
  "E2_EVENT_PROVENANCE_UNVERIFIED", "EVENT_SNAPSHOT_EMPTY",
  "EVOLUTION_DATA_GAP_INVALID", "EVOLUTION_DATA_LIMIT_EXCEEDED",
  "EVOLUTION_DATA_RANGE_INVALID", "EVOLUTION_DATA_STALE",
  "strategy_candidate_rejected", "strategy_candidate_not_accepted",
  "strategy_candidate_missing_backtest_context", "paper_runner_missing_market_context",
  "backtest_not_completed", "backtest_missing_market_context", "backtest_strategy_not_e1_seedable",
  "e1_run_missing_frozen_config", "event_campaign_timeframe_unsupported", "e1_run_not_completed",
  "e1_candidate_not_seedable", "e1_parent_run_not_completed",
]);

/** Carry only non-secret input blocker codes across the approval middleware. */
export class EvolutionInputBlockedError extends Error {
  readonly code = "E2_INPUT_BLOCKED";
  readonly blocker_codes: string[];

  constructor(codes: string[]) {
    const blockers = codes.filter(code => INPUT_BLOCKER_CODES.has(code));
    const normalized = blockers.length ? [...new Set(blockers)] : [codes.length ? "E2_INPUT_PREFLIGHT_INVALID" : "E2_INPUT_COVERAGE_INSUFFICIENT"];
    super(`E2_INPUT_BLOCKED: ${normalized.join(", ")}; inspect preparation; no generation started`);
    this.name = "EvolutionInputBlockedError";
    this.blocker_codes = normalized;
  }
}
