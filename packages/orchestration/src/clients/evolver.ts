/** services/evolver 的 owner-scoped API 客户端。 */
import { createHash } from "node:crypto";

import { HttpClient, HttpClientError } from "./http.js";
import type { EvolutionLLMSnapshot } from "../mastra/llm/evolution-snapshot.js";

export type EvolutionConfig = {
  venue: string;
  symbol: string;
  timeframe: string;
  from_ts: string;
  as_of: string;
  initial_cash?: number;
  fee_rate?: number;
  validation_split?: number;
  trading_mode?: "spot" | "perp";
  leverage?: number;
  params?: Record<string, unknown>;
  funding_rate?: number;
};

export type EvolutionStartRequest = {
  budget: number;
  seed_strategy_id: string;
  config: Required<EvolutionConfig>;
  llm: EvolutionLLMSnapshot;
};

export type EvolutionLoopStartRequest = {
  baseline: EvolutionStartRequest;
  campaign: EventCampaignRequest;
  max_cost_usd: number;
};

/** Covers both stage intents and the hard dollar cap using the Python canonical form. */
export function evolutionLoopRequestDigest(request: EvolutionLoopStartRequest): string {
  return createHash("sha256").update(JSON.stringify([
    "evolution-loop-v1", evolutionRequestDigest(request.baseline),
    eventCampaignRequestDigest(request.campaign), float64Hex(request.max_cost_usd),
  ])).digest("hex");
}

export type EventCampaignRequest = {
  event_snapshot_id: string;
  source_run_id: string | null;
  target_kind: "strategy_candidate" | "paper_runner" | "backtest_run" | "e1_run" | "e1_candidate" | "e2_campaign" | null;
  target_id: string | null;
  config: {
    venue: string;
    symbol: string;
    asset_id: string;
    event_asset_code: string;
    timeframe: "15m" | "1h" | "4h";
    from_ts: string;
    as_of: string;
    initial_cash: number;
    fee_rate: number;
    funding_rate?: number;
    trading_mode: "spot" | "perp";
    leverage: number;
    discovery_ratio: 0.6;
    generation_validation_ratio: 0.2;
    sealed_holdout_ratio: 0.2;
    execution_model_version: "event-fill-v1";
    control_matcher_version: "event-control-v1";
    random_seed: number;
  };
  llm: EvolutionLLMSnapshot;
  hypotheses: [];
};

export type EventCampaignResult = {
  campaign_id: string;
  status: string;
  active_generation: number;
  max_generations: number;
  event_snapshot_id: string;
  llm_cost_usd: number;
  source_run_id?: string | null;
  frozen_config?: Record<string, unknown>;
  locked_candidate_id?: string | null;
  forward_event_count?: number;
  failure_code?: string | null;
  failure_message?: string | null;
  state_version?: number;
};

export type EvolutionLoopResult = {
  loop_id: string;
  operation_id: string;
  target_kind: string;
  target_id: string;
  status: string;
  e1_run_id: string | null;
  campaign_id: string | null;
  forward_sandbox_id: string | null;
  holdout_attempt_id: string | null;
  failure_code: string | null;
  failure_message: string | null;
  state_version: number;
};

export type EventEvolutionCapabilities = {
  event_evolution_enabled: boolean;
  durable_loop_enabled?: boolean;
  durable_loop_reason?: string | null;
  reason: string | null;
  max_generations: 5;
  hypotheses_per_generation: 8;
  implementations_per_hypothesis: 3;
  candidate_concurrency: number;
  supported_timeframes: Array<"15m" | "1h" | "4h">;
  automatic_stage_approval: true;
  automatic_promotion: false;
  runner_eligible: false;
};

type EventCampaignConfigBase = Omit<
  EventCampaignRequest["config"],
  "discovery_ratio" | "generation_validation_ratio" | "sealed_holdout_ratio" | "execution_model_version" | "control_matcher_version"
>;
export type EventCampaignConfigInput = Omit<
  EventCampaignConfigBase,
  "initial_cash" | "fee_rate" | "trading_mode" | "leverage" | "random_seed"
> & Partial<Pick<EventCampaignConfigBase, "initial_cash" | "fee_rate" | "trading_mode" | "leverage" | "random_seed">>;

/** Build the fixed 60/20/20 automatic event campaign request. */
export function buildEventCampaignRequest(options: {
  eventSnapshotId: string;
  sourceRunId?: string;
  targetKind?: NonNullable<EventCampaignRequest["target_kind"]>;
  targetId?: string;
  config: EventCampaignConfigInput;
  llmSnapshot: EvolutionLLMSnapshot;
}): EventCampaignRequest {
  return {
    event_snapshot_id: options.eventSnapshotId,
    source_run_id: options.sourceRunId ?? null,
    target_kind: options.targetKind ?? null,
    target_id: options.targetId ?? null,
    config: {
      ...options.config,
      from_ts: new Date(options.config.from_ts).toISOString(),
      as_of: new Date(options.config.as_of).toISOString(),
      initial_cash: options.config.initial_cash ?? 10_000,
      fee_rate: options.config.fee_rate ?? 0.001,
      funding_rate: options.config.funding_rate ?? 0,
      trading_mode: options.config.trading_mode ?? "perp",
      leverage: options.config.leverage ?? 1,
      random_seed: options.config.random_seed ?? 0,
      discovery_ratio: 0.6,
      generation_validation_ratio: 0.2,
      sealed_holdout_ratio: 0.2,
      execution_model_version: "event-fill-v1",
      control_matcher_version: "event-control-v1",
    },
    llm: options.llmSnapshot,
    hypotheses: [],
  };
}

/** Match Python's sorted compact JSON digest for the auto-campaign request. */
export function eventCampaignRequestDigest(request: EventCampaignRequest): string {
  const config = request.config;
  const hypothesesHash = createHash("sha256").update("[]").digest("hex");
  const canonical: unknown[] = [
    request.target_kind ?? "",
    request.target_id ?? "",
    request.event_snapshot_id,
    request.source_run_id ?? "",
    config.venue,
    config.symbol,
    config.asset_id,
    config.event_asset_code,
    config.timeframe,
    numberText(Date.parse(config.from_ts)),
    numberText(Date.parse(config.as_of)),
    float64Hex(config.initial_cash),
    float64Hex(config.fee_rate),
    config.trading_mode,
    numberText(config.leverage),
    float64Hex(config.discovery_ratio),
    float64Hex(config.generation_validation_ratio),
    float64Hex(config.sealed_holdout_ratio),
    config.execution_model_version,
    config.control_matcher_version,
    numberText(config.random_seed),
    request.llm.config_digest,
    hypothesesHash,
  ];
  if (config.funding_rate) canonical.push(["funding_rate", float64Hex(config.funding_rate)]);
  return createHash("sha256").update(JSON.stringify(canonical)).digest("hex");
}

/** 将 tool 输入收口为签名与发送共用的唯一请求体。 */
export function buildEvolutionStartRequest(options: {
  budget?: number;
  seedStrategyId?: string;
  config: EvolutionConfig;
  llmSnapshot: EvolutionLLMSnapshot;
}): EvolutionStartRequest {
  return {
    budget: options.budget ?? 4,
    seed_strategy_id: options.seedStrategyId ?? "sma_cross_v1",
    config: {
      ...options.config,
      from_ts: new Date(options.config.from_ts).toISOString(),
      as_of: new Date(options.config.as_of).toISOString(),
      initial_cash: options.config.initial_cash ?? 10_000,
      fee_rate: options.config.fee_rate ?? 0.001,
      validation_split: options.config.validation_split ?? 0.3,
      params: options.config.params ?? {},
      funding_rate: options.config.funding_rate ?? 0,
      trading_mode: options.config.trading_mode ?? "spot",
      leverage: options.config.leverage ?? 1,
    },
    llm: options.llmSnapshot,
  };
}

/** 生成与 Python 端一致的审批请求摘要，覆盖所有会影响成本或结果的字段。 */
export function evolutionRequestDigest(request: EvolutionStartRequest): string {
  const config = request.config;
  const canonical: unknown[] = [
    request.seed_strategy_id,
    numberText(request.budget),
    config.venue,
    config.symbol,
    config.timeframe,
    numberText(Date.parse(config.from_ts)),
    numberText(Date.parse(config.as_of)),
    float64Hex(config.initial_cash),
    float64Hex(config.fee_rate),
    float64Hex(config.validation_split),
    config.trading_mode,
    numberText(config.leverage),
    request.llm.config_digest,
  ];
  if (Object.keys(config.params ?? {}).length) canonical.push(canonicalParams(config.params));
  if (config.funding_rate) canonical.push(["funding_rate", float64Hex(config.funding_rate)]);
  return createHash("sha256").update(JSON.stringify(canonical)).digest("hex");
}

/** Canonicalize constructor parameters without depending on JSON number spelling or key order. */
function canonicalParams(value: unknown): unknown {
  if (Array.isArray(value)) return ["array", value.map(canonicalParams)];
  if (value !== null && typeof value === "object") {
    const record = value as Record<string, unknown>;
    return ["object", Object.keys(record).sort((a, b) => Buffer.compare(Buffer.from(a), Buffer.from(b)))
      .map((key) => [key, canonicalParams(record[key])])];
  }
  if (typeof value === "number") return ["number", float64Hex(value)];
  return value;
}

function numberText(value: number): string {
  if (!Number.isFinite(value)) throw new Error("evolution request contains a non-finite number");
  return String(value);
}

function float64Hex(value: number): string {
  if (!Number.isFinite(value)) throw new Error("evolution request contains a non-finite number");
  const buffer = Buffer.allocUnsafe(8);
  buffer.writeDoubleBE(value);
  return buffer.toString("hex");
}

export type CandidateResult = {
  candidate_id: string;
  run_id: string;
  slot: number;
  generation: number;
  stage: string;
  outcome: string;
  source_code: string | null;
  source_hash: string | null;
  unified_diff: string | null;
  mutation_hint: string | null;
  llm_cost_usd: number | null;
  fitness: number | null;
  evaluation_snapshot: Record<string, unknown> | null;
  audit_snapshot: Record<string, unknown> | null;
  contract_snapshot: Record<string, unknown> | null;
  error_code: string | null;
  error_message: string | null;
  overfitting_risk: string;
  created_at: string | null;
  updated_at: string | null;
};

export type RunStatusResult = {
  run_id: string;
  seed_strategy_id: string;
  budget: number;
  config: Record<string, unknown>;
  llm_snapshot: EvolutionLLMSnapshot | null;
  llm_config_digest: string | null;
  status: "queued" | "running" | "cancelling" | "completed" | "failed" | "aborted";
  active_stage: string | null;
  llm_cost_usd: number;
  queued_at: string;
  started_at: string | null;
  finished_at: string | null;
  dataset_manifest: Record<string, unknown> | null;
  seed_report_snapshot: Record<string, unknown> | null;
  baseline_snapshot: Record<string, unknown> | null;
  failure_code: string | null;
  failure_message: string | null;
  attempted: number;
  succeeded: number;
  rejected: number;
  candidates: CandidateResult[];
};

export type RunListResult = { items: RunStatusResult[]; next_cursor: string | null };

export class EvolverClient {
  private readonly http: HttpClient;

  /** Read an owner-scoped durable workflow without advancing or restarting it. */
  async getEvolutionLoop(loopId: string): Promise<EvolutionLoopResult> {
    return await this.http.get<EvolutionLoopResult>(`/api/v1/evolution-loops/${loopId}`);
  }

  constructor(options: { baseUrl: string; token: string; timeoutMs?: number }) {
    this.http = new HttpClient(options);
  }

  /** Resolve a target to an owned workflow without creating or charging anything. */
  async findEvolutionLoop(targetKind: string, targetId: string): Promise<EvolutionLoopResult | null> {
    return await this.http.get<EvolutionLoopResult | null>("/api/v1/evolution-loops/for-target", {
      target_kind: targetKind, target_id: targetId,
    });
  }

  /** Start the baseline and its automatic continuation with one bounded authorization. */
  async startEvolutionLoop(options: {
    request: EvolutionLoopStartRequest;
    idempotencyKey: string;
    credentialGrant: string;
  }): Promise<EvolutionLoopResult> {
    return await this.http.post<EvolutionLoopResult>("/api/v1/evolution-loops/start", options.request, {
      "Idempotency-Key": options.idempotencyKey,
      "X-Evolution-Credential": options.credentialGrant,
    });
  }

  async startRun(options: {
    request: EvolutionStartRequest;
    idempotencyKey: string;
    credentialGrant: string;
  }): Promise<RunStatusResult> {
    const headers = {
      "Idempotency-Key": options.idempotencyKey,
      "X-Evolution-Credential": options.credentialGrant,
    };
    try {
      return await this.http.post<RunStatusResult>("/api/v1/runs", options.request, headers);
    } catch (error) {
      if (!(error instanceof HttpClientError) || ![502, 504].includes(error.status)) throw error;
      return await this.http.post<RunStatusResult>("/api/v1/runs", options.request, headers);
    }
  }

  /** Create and start one automatic five-generation event campaign. */
  async startEventCampaign(options: {
    request: EventCampaignRequest;
    idempotencyKey: string;
    credentialGrant: string;
  }): Promise<EvolutionLoopResult> {
    const headers = {
      "Idempotency-Key": options.idempotencyKey,
      "X-Evolution-Credential": options.credentialGrant,
    };
    return await this.http.post<EvolutionLoopResult>(
      "/api/v1/evolution-loops",
      options.request,
      headers,
    );
  }

  async getEventCampaign(campaignId: string): Promise<EventCampaignResult> {
    return await this.http.get<EventCampaignResult>(`/api/v1/campaigns/${campaignId}`);
  }

  async getEventEvolutionCapabilities(): Promise<EventEvolutionCapabilities> {
    return await this.http.get<EventEvolutionCapabilities>("/api/v1/capabilities");
  }

  async listRuns(limit = 20): Promise<RunListResult> {
    return await this.http.get<RunListResult>("/api/v1/runs", { limit });
  }

  async getRun(runId: string): Promise<RunStatusResult> {
    return await this.http.get<RunStatusResult>(`/api/v1/runs/${runId}`);
  }

  async getCandidate(candidateId: string): Promise<CandidateResult> {
    return await this.http.get<CandidateResult>(`/api/v1/candidates/${candidateId}`);
  }

  async abortRun(runId: string): Promise<RunStatusResult> {
    return await this.http.post<RunStatusResult>(`/api/v1/runs/${runId}/abort`, {});
  }
}
