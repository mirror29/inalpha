/**
 * services/factor 客户端 —— 接现成因子库（pandas-ta / Alpha101 / qlib Alpha158）+
 * 有效性打分（前瞻收益 / Rank IC）。
 *
 * 单职责小客户端，与 ``DataClient`` / ``ResearchClient`` 同构。
 */
import { HttpClient } from "./http.js";

/** 因子目录里的单个定义 */
export type FactorSpec = {
  factor_id: string;
  source: string;
  name: string;
  kind: string;
  needs_universe: boolean;
  direction_hint: number;
  available: boolean;
  extras?: Record<string, string>;
};

export type CatalogResult = {
  factors: FactorSpec[];
  sources: Record<string, boolean>;
};

/** 单因子有效性 */
export type FactorEffectiveness = {
  factor_id: string;
  source: string;
  name: string;
  kind: string;
  value: number | null;
  rank_ic: number;
  rank_ic_recent: number;
  icir: number;
  turnover: number;
  sample_size: number;
  corr_pruned?: string[];
  quantile_returns: { q: number; mean_return: number; sample_size: number }[];
  long_short_return: number;
  direction: number;
  strength: number;
  low_confidence: boolean;
  decay_state?: "stable" | "fading" | "decaying";
};

export type ScoreResult = {
  venue: string;
  symbol: string;
  timeframe: string;
  as_of: string;
  horizon_bars: number;
  bars_used: number;
  factors: FactorEffectiveness[];
  ic_null_benchmark?: number;
};

export type SnapshotResult = {
  venue: string;
  symbol: string;
  timeframe: string;
  as_of: string;
  horizon_bars: number;
  bars_used: number;
  available: boolean;
  reason: string | null;
  top_factors: FactorEffectiveness[];
  candidates_evaluated: number;
  low_confidence_count: number;
  ic_null_benchmark?: number;
};

export type CustomScoreResult = {
  venue: string;
  symbol: string;
  timeframe: string;
  as_of: string;
  horizon_bars: number;
  bars_used: number;
  available: boolean;
  reason: string | null;
  expression: string;
  factor: FactorEffectiveness | null;
  ic_pvalue: number | null;
  top_correlated: { factor_id: string; corr: number }[];
  max_corr: number | null;
  is_likely_redundant: boolean;
  /** P5: 多标的并发评估结果 */
  multi_symbol?: {
    per_symbol: Record<string, { rank_ic: number | null; available: boolean }>;
    cross_symbol_ic_mean: number | null;
    cross_symbol_ic_std: number | null;
    cross_symbol_consistency: number | null;
    n_symbols_evaluated: number;
    n_symbols_failed: number;
  } | null;
};

export type PanelRankEntry = {
  symbol: string;
  value: number;
  rank_pct: number;
};

export type PanelFactorResult = {
  factor_id: string;
  source: string;
  name: string;
  kind: string;
  ic_kind: string;
  cross_sectional_ic: number;
  icir: number;
  n_periods: number;
  mean_valid_symbols: number;
  low_confidence: boolean;
  latest_ranking_ts: string | null;
  latest_ranking: PanelRankEntry[];
};

export type PanelScoreResult = {
  venue: string;
  timeframe: string;
  as_of: string;
  horizon_bars: number;
  symbols: string[];
  bars_used: Record<string, number>;
  latest_bar_ts: Record<string, string | null>;
  is_pit: boolean;
  universe_note: string;
  factors: PanelFactorResult[];
  ic_null_benchmark?: number;
  reason: string | null;
  unknown_factor_ids: string[];
};

export type FactorCandidateRecord = {
  id: string;
  expression: string;
  expression_hash: string;
  name: string | null;
  hypothesis: string;
  proposed_by: string;
  venue: string | null;
  symbol: string | null;
  timeframe: string | null;
  test_results: Record<string, unknown>;
  batch_id: string | null;
  n_tested: number;
  status: "pending_review" | "rejected" | "registered";
  reviewed_by: string | null;
  reviewed_at: string | null;
  review_note: string | null;
  created_at: string;
  updated_at: string;
};

export type ProposeFactorResult = {
  candidate_id: string;
  expression_hash: string;
  created: boolean;
  status: string;
};

export type BacktestScoreResult = {
  venue: string;
  symbol: string;
  timeframe: string;
  as_of: string;
  horizon_bars: number;
  bars_used: number;
  available: boolean;
  reason: string | null;
  expression: string;
  factor: FactorEffectiveness | null;
  ic_pvalue: number | null;
  top_correlated: { factor_id: string; corr: number }[];
  max_corr: number | null;
  is_likely_redundant: boolean;
  backtest: {
    oos_sharpe: number | null;
    oos_sharpe_p5: number | null;
    oos_sharpe_p95: number | null;
    oos_max_drawdown_pct: number | null;
    oos_win_rate: number | null;
    oos_return_pct: number | null;
    baseline_sharpe: number | null;
    dsr: number | null;
    n_paths: number;
    splitter_used: string;
  } | null;
};

export class FactorClient {
  private readonly http: HttpClient;

  constructor(opts: { baseUrl: string; token: string; timeoutMs?: number }) {
    this.http = new HttpClient({
      baseUrl: opts.baseUrl,
      token: opts.token,
      timeoutMs: opts.timeoutMs ?? 60_000,
    });
  }

  async health(): Promise<Record<string, unknown>> {
    return await this.http.get("/health");
  }

  async catalog(): Promise<CatalogResult> {
    return await this.http.get<CatalogResult>("/catalog");
  }

  async score(params: Record<string, unknown>): Promise<ScoreResult> {
    return await this.http.post<ScoreResult>("/score", params);
  }

  async snapshot(params: Record<string, unknown>): Promise<SnapshotResult> {
    return await this.http.post<SnapshotResult>("/snapshot", params);
  }

  async panelScore(params: Record<string, unknown>): Promise<PanelScoreResult> {
    return await this.http.post<PanelScoreResult>("/panel/score", params);
  }

  async customScore(params: Record<string, unknown>): Promise<CustomScoreResult> {
    return await this.http.post<CustomScoreResult>("/custom/score", params);
  }

  async proposeCandidate(params: Record<string, unknown>): Promise<ProposeFactorResult> {
    return await this.http.post<ProposeFactorResult>("/candidates", params);
  }

  async listCandidates(params: Record<string, unknown> = {}): Promise<FactorCandidateRecord[]> {
    return await this.http.get<FactorCandidateRecord[]>("/candidates", params as Record<string, string | number | boolean | undefined>);
  }

  async backtestScore(params: {
    expression: string;
    name?: string;
    venue: string;
    symbol: string;
    timeframe: string;
    asOf?: string;
    lookbackBars?: number;
    horizonBars?: number;
    initialCash?: number;
    feeRate?: number;
    cvSplitter?: string;
    cvNFolds?: number;
  }): Promise<BacktestScoreResult> {
    return await this.http.post<BacktestScoreResult>("/backtest/score", {
      expression: params.expression,
      name: params.name,
      venue: params.venue,
      symbol: params.symbol,
      timeframe: params.timeframe,
      as_of: params.asOf,
      lookback_bars: params.lookbackBars,
      horizon_bars: params.horizonBars,
      initial_cash: params.initialCash,
      fee_rate: params.feeRate,
      cv_splitter: params.cvSplitter,
      cv_n_folds: params.cvNFolds,
    });
  }
}