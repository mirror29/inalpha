/** Read only the non-secret, server-prepared fields used in an E1 approval summary. */
export function evolutionApprovalSummary(envelope: unknown) {
  const root = object(envelope);
  const input = object(root?.toolInput);
  const request = object(input?.request);
  const config = object(request?.config);
  const model = object(input?.llm_snapshot);
  const preparation = object(root?.preparation ?? input?.preparation);
  const dataset = object(preparation?.dataset_manifest);
  if (!request || !config || !model || !preparation || !dataset) return null;
  const reference = text(preparation.seed_strategy_id) ?? text(request.seedStrategyId);
  if (!reference) return null;
  const context = object(preparation.seed_context);
  const retry = text(request.retryOfRunId);
  const kind = retry ? "retry" : reference === "sma_cross_v1" ? "builtin"
    : reference.startsWith("evolution_candidate:") ? "evolution" : "candidate";
  return {
    kind, label: text(context?.seed_label)?.slice(0, 120) ?? null,
    slot: number(context?.slot), reference,
    market: [text(config.venue), text(config.symbol)].filter(Boolean).join(" · "),
    timeframe: text(config.timeframe), mode: config.trading_mode === "perp" ? "perp" : config.trading_mode === "spot" ? "spot" : null,
    from: timestamp(config.from_ts), to: timestamp(config.as_of),
    budget: integer(request.budget, 1, 20), bars: integer(dataset.bar_count, 2, 10000),
    latestBar: timestamp(dataset.latest_bar_ts), validationSplit: fraction(config.validation_split, 0.5),
    initialCash: number(config.initial_cash), feeRate: fraction(config.fee_rate, 0.1), leverage: integer(config.leverage, 1, 20),
    model: [text(model.provider), text(model.model)].filter(Boolean).join(" / "),
    estimatedCost: number(preparation.estimated_max_cost_usd),
    warningCount: Array.isArray(dataset.warnings) ? dataset.warnings.length : 0,
    seedHash: digest(preparation.seed_source_hash), datasetHash: digest(dataset.content_sha256),
    requestHash: digest(preparation.request_digest),
  };
}

/** Reject arrays and scalar payloads before inspecting approval metadata. */
function object(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : null;
}

/** Bound display text; React escapes it instead of interpreting it as markup. */
function text(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim().slice(0, 200) : null;
}

/** Missing and invalid amounts remain unavailable, including legacy receipts. */
function number(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : null;
}

/** Show approved timestamps explicitly in UTC. */
function timestamp(value: unknown): string | null {
  const parsed = typeof value === "string" ? Date.parse(value) : Number.NaN;
  return Number.isFinite(parsed) ? new Date(parsed).toISOString().slice(0, 16).replace("T", " ") + " UTC" : null;
}

/** Hashes belong in the diagnostic disclosure only. */
function digest(value: unknown): string | null {
  return typeof value === "string" && /^[0-9a-f]{64}$/i.test(value) ? value : null;
}


/** Counts must fall inside the service's accepted integer bounds. */
function integer(value: unknown, minimum: number, maximum: number): number | null {
  return typeof value === "number" && Number.isInteger(value) && value >= minimum && value <= maximum ? value : null;
}

/** Do not turn malformed percentages into plausible approval details. */
function fraction(value: unknown, maximum: number): number | null {
  const parsed = number(value);
  return parsed !== null && parsed <= maximum ? parsed : null;
}
