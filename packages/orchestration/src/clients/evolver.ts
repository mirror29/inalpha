/**
 * services/evolver 客户端 —— 策略演化引擎。
 *
 * 封装 evolver 服务（`POST /api/v1/runs` / `GET /api/v1/runs/{run_id}` / `GET /api/v1/candidates/{candidate_id}`）。
 */
import { HttpClient } from "./http.js";

/** 演化运行配置（与 evolver API schema 对齐）。 */
export type EvolutionConfig = {
  universe?: string[];
  period_from?: string;
  period_to?: string;
  timeframe?: string;
  initial_cash?: number;
};

/** 候选策略响应（与 evolver API CandidateResponse 对齐）。 */
export type CandidateResult = {
  candidate_id: string;
  run_id: string;
  generation: number;
  parent_id: string | null;
  source_code: string;
  source_hash: string;
  mutation_hint: string | null;
  fitness: number | null;
  report: Record<string, unknown> | null;
  overfitting_risk: string;
  status: string;
  created_at: string | null;
};

/** 运行状态响应（与 evolver API RunStatusResponse 对齐）。 */
export type RunStatusResult = {
  run_id: string;
  seed_strategy_id: string;
  budget: number;
  config: Record<string, unknown> | null;
  status: string;
  llm_cost_usd: number;
  candidates_count: number;
  rejected_ast: number;
  rejected_contract: number;
  failed_eval: number;
  started_at: string | null;
  finished_at: string | null;
  candidates: CandidateResult[];
};

export class EvolverClient {
  private readonly http: HttpClient;

  constructor(options: { baseUrl: string; token: string; timeoutMs?: number }) {
    this.http = new HttpClient(options);
  }

  /**
   * 启动一次演化运行。
   *
   * @param budget 变异预算数（候选数量，默认 4）
   * @param seedStrategyId 种子策略 ID（默认 "sma_cross_v1"）
   * @param config 演化配置（universe, period, timeframe, initial_cash）
   * @returns 运行状态（含 run_id 用于后续轮询）
   */
  async startRun(
    budget?: number,
    seedStrategyId?: string,
    config?: EvolutionConfig,
  ): Promise<RunStatusResult> {
    return await this.http.post<RunStatusResult>("/api/v1/runs", {
      budget: budget ?? 4,
      seed_strategy_id: seedStrategyId ?? "sma_cross_v1",
      config: config ?? undefined,
    });
  }

  /**
   * 查询演化运行状态。
   *
   * @param runId 运行 UUID
   * @returns 运行状态（含候选列表，按 fitness 降序）
   */
  async getRun(runId: string): Promise<RunStatusResult> {
    return await this.http.get<RunStatusResult>(`/api/v1/runs/${runId}`);
  }

  /**
   * 查询单个候选策略详情。
   *
   * @param candidateId 候选 UUID
   * @returns 候选信息（含源码 + 报告 + fitness）
   */
  async getCandidate(candidateId: string): Promise<CandidateResult> {
    return await this.http.get<CandidateResult>(`/api/v1/candidates/${candidateId}`);
  }
}