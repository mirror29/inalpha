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
};

export type EvolutionStartRequest = {
  budget: number;
  seed_strategy_id: string;
  config: Required<EvolutionConfig>;
  llm: EvolutionLLMSnapshot;
};

export type EventCampaignRequest = {
  event_snapshot_id: string;
  source_run_id: string | null;
  config: {
    venue: string;
    symbol: string;
    timeframe: "15m" | "1h" | "4h";
    from_ts: string;
    as_of: string;
    initial_cash: number;
    fee_rate: number;
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
  config: EventCampaignConfigInput;
  llmSnapshot: EvolutionLLMSnapshot;
}): EventCampaignRequest {
  return {
    event_snapshot_id: options.eventSnapshotId,
    source_run_id: options.sourceRunId ?? null,
    config: {
      ...options.config,
      from_ts: new Date(options.config.from_ts).toISOString(),
      as_of: new Date(options.config.as_of).toISOString(),
      initial_cash: options.config.initial_cash ?? 10_000,
      fee_rate: options.config.fee_rate ?? 0.001,
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
  const canonical = [
    request.event_snapshot_id,
    request.source_run_id ?? "",
    config.venue,
    config.symbol,
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
    },
    llm: options.llmSnapshot,
  };
}

/** 生成与 Python 端一致的审批请求摘要，覆盖所有会影响成本或结果的字段。 */
export function evolutionRequestDigest(request: EvolutionStartRequest): string {
  const config = request.config;
  const canonical = [
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
    request.llm.config_digest,
  ];
  return createHash("sha256").update(JSON.stringify(canonical)).digest("hex");
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

  constructor(options: { baseUrl: string; token: string; timeoutMs?: number }) {
    this.http = new HttpClient(options);
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
  }): Promise<EventCampaignResult> {
    const headers = {
      "Idempotency-Key": options.idempotencyKey,
      "X-Evolution-Credential": options.credentialGrant,
    };
    const created = await this.http.post<EventCampaignResult>(
      "/api/v1/campaigns",
      options.request,
      headers,
    );
    if (created.status !== "draft") return created;

    try {
      return await this.http.post<EventCampaignResult>(
        `/api/v1/campaigns/${created.campaign_id}/start`,
        {},
      );
    } catch (error) {
      if (!(error instanceof HttpClientError) || error.code !== "CAMPAIGN_STATE_CONFLICT") {
        throw error;
      }
      const current = await this.getEventCampaign(created.campaign_id);
      if (current.status === "draft") throw error;
      return current;
    }
  }

  async getEventCampaign(campaignId: string): Promise<EventCampaignResult> {
    return await this.http.get<EventCampaignResult>(`/api/v1/campaigns/${campaignId}`);
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
