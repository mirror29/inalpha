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
  /** 附加约束，如 macro 因子的 timeframes（仅 1d/1wk）与 FRED series id */
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
  /** 近 1/3 样本窗 Rank IC：与 rank_ic 反号/趋零 = 因子正在衰减（ADR-0043） */
  rank_ic_recent: number;
  icir: number;
  /** 因子换手 0-1：高 IC + 高换手的信号应打折 */
  turnover: number;
  sample_size: number;
  /** snapshot 去相关时被本因子挤掉的同质因子 id */
  corr_pruned?: string[];
  quantile_returns: { q: number; mean_return: number; sample_size: number }[];
  long_short_return: number;
  direction: number; // +1/-1/0
  strength: number; // 0-1
  low_confidence: boolean;
  /**
   * 衰减三态（ADR-0047 服务端单一权威）：decaying=recent 反号/趋零；stable=量级
   * 保住 60%+；fading=其间。设计策略时把它填进 author_strategy 的 factorContext。
   */
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
  /**
   * 选择效应基准：N 个候选、当前样本量下纯噪声的期望最大 |IC|。
   * top 因子 |rank_ic| 不显著高于此值 ⇒ 可能是选择效应（地板，不是假设检验）。
   */
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
  /** top-N 是从多少个候选里挑的（多重检验背景：候选越多，最高 |IC| 的期望越虚高） */
  candidates_evaluated: number;
  /** 样本不足被排除排序的因子数 */
  low_confidence_count: number;
  /** 选择效应基准（同 ScoreResult.ic_null_benchmark） */
  ic_null_benchmark?: number;
};

/** POST /custom/score —— 自定义表达式因子的一站式评估结果（D-12 · 因子发现 L1）。 */
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
  /** factor_id=custom.<sha16>；available=false 时为 null */
  factor: FactorEffectiveness | null;
  /** rank_ic 双侧 p 值（t 近似，参考量级非严格检验） */
  ic_pvalue: number | null;
  /** 与库内价量因子的 |spearman| top5——查重复造轮子 */
  top_correlated: { factor_id: string; corr: number }[];
  max_corr: number | null;
  /** max_corr ≥ 去相关阈值——大概率是已有因子换皮 */
  is_likely_redundant: boolean;
};

/** POST /panel/score —— 横截面因子结果。 */
export type PanelRankEntry = {
  symbol: string;
  /** 该标的最近有效横截面时点的因子值 */
  value: number;
  /** 该值在 universe 内的分位排名 (0,1]，升序 */
  rank_pct: number;
};

export type PanelFactorResult = {
  factor_id: string;
  source: string;
  name: string;
  kind: string;
  /** 恒 "cross_sectional"，与单标的 timeseries IC 区分 */
  ic_kind: string;
  /** 逐期横截面 rank-IC 均值：每期对全池按因子排序 vs 跨标的前瞻收益 */
  cross_sectional_ic: number;
  icir: number;
  /** 参与的横截面期数（有效标的 ≥ min_symbols 的 t） */
  n_periods: number;
  mean_valid_symbols: number;
  low_confidence: boolean;
  /** latest_ranking 基于哪一期横截面的 ISO ts（fresh=False 下可能比 as_of 旧几天） */
  latest_ranking_ts: string | null;
  /** 最近有效横截面排名（按因子值升序）——选标的直接用：取最低=首，最高=尾 */
  latest_ranking: PanelRankEntry[];
};

export type PanelScoreResult = {
  venue: string;
  timeframe: string;
  as_of: string;
  horizon_bars: number;
  symbols: string[];
  bars_used: Record<string, number>;
  /** 每标的最后一根 bar 的 ISO ts（null=无数据）。**判新鲜看它距 as_of 的间隔,不看 bar 数** */
  latest_bar_ts: Record<string, string | null>;
  /** universe 是否 PIT。**当前恒 false**（成分快照未建，带存活者偏差，证据打折） */
  is_pit: boolean;
  universe_note: string;
  factors: PanelFactorResult[];
  ic_null_benchmark?: number;
  reason: string | null;
  /** 传入 factor_ids 里不在 catalog 的（拼错/过期）；恒透出，空 = 全部有效 */
  unknown_factor_ids: string[];
};

/** factor_candidates 一行（D-12 · 因子发现 L1）。 */
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
  /** false = 撞同表达式已有候选，返老行（幂等） */
  created: boolean;
  status: string;
};

export class FactorClient {
  private readonly http: HttpClient;

  constructor(opts: { baseUrl: string; token: string; timeoutMs?: number }) {
    // 有效性要拉数百根 bar + 跨多因子算 Rank IC，比单查 /bars 慢；给 60s 余量
    this.http = new HttpClient({
      baseUrl: opts.baseUrl,
      token: opts.token,
      timeoutMs: opts.timeoutMs ?? 60_000,
    });
  }

  async health(): Promise<{
    status: string;
    service: string;
    version: string;
    qlib_enabled: boolean;
    adapters: Record<string, boolean>;
  }> {
    return await this.http.get("/health");
  }

  async catalog(): Promise<CatalogResult> {
    return await this.http.get<CatalogResult>("/catalog");
  }

  async score(params: {
    venue: string;
    symbol: string;
    timeframe: string;
    asOf?: string;
    lookbackBars?: number;
    horizonBars?: number;
    quantiles?: number;
    factorIds?: string[];
  }): Promise<ScoreResult> {
    return await this.http.post<ScoreResult>("/score", {
      venue: params.venue,
      symbol: params.symbol,
      timeframe: params.timeframe,
      as_of: params.asOf,
      lookback_bars: params.lookbackBars,
      horizon_bars: params.horizonBars,
      quantiles: params.quantiles,
      factor_ids: params.factorIds,
    });
  }

  async snapshot(params: {
    venue: string;
    symbol: string;
    timeframe: string;
    asOf?: string;
    lookbackBars?: number;
    horizonBars?: number;
    topN?: number;
  }): Promise<SnapshotResult> {
    return await this.http.post<SnapshotResult>("/snapshot", {
      venue: params.venue,
      symbol: params.symbol,
      timeframe: params.timeframe,
      as_of: params.asOf,
      lookback_bars: params.lookbackBars,
      horizon_bars: params.horizonBars,
      top_n: params.topN,
    });
  }

  /** 一篮子标的的横截面因子评估（横截面 rank-IC + 最新排名,选标的用）。 */
  async panelScore(params: {
    venue: string;
    symbols?: string[];
    indexCode?: string;
    timeframe: string;
    asOf?: string;
    lookbackBars?: number;
    horizonBars?: number;
    minSymbols?: number;
    factorIds?: string[];
  }): Promise<PanelScoreResult> {
    return await this.http.post<PanelScoreResult>("/panel/score", {
      venue: params.venue,
      symbols: params.symbols ?? [],
      index_code: params.indexCode,
      timeframe: params.timeframe,
      as_of: params.asOf,
      lookback_bars: params.lookbackBars,
      horizon_bars: params.horizonBars,
      min_symbols: params.minSymbols,
      factor_ids: params.factorIds,
    });
  }

  /** D-12 · 因子发现 L1：评估一个自定义 DSL 表达式因子（一次出 effectiveness+p值+库相关性）。 */
  async customScore(params: {
    expression: string;
    name?: string;
    venue: string;
    symbol: string;
    timeframe: string;
    asOf?: string;
    lookbackBars?: number;
    horizonBars?: number;
    quantiles?: number;
  }): Promise<CustomScoreResult> {
    return await this.http.post<CustomScoreResult>("/custom/score", {
      expression: params.expression,
      name: params.name,
      venue: params.venue,
      symbol: params.symbol,
      timeframe: params.timeframe,
      as_of: params.asOf,
      lookback_bars: params.lookbackBars,
      horizon_bars: params.horizonBars,
      quantiles: params.quantiles,
    });
  }

  /** D-12：把通过评估的表达式提为候选（status=pending_review，人工审核才 registered）。 */
  async proposeCandidate(params: {
    expression: string;
    hypothesis: string;
    name?: string;
    proposedBy?: string;
    venue?: string;
    symbol?: string;
    timeframe?: string;
    testResults?: Record<string, unknown>;
    batchId?: string;
    nTested?: number;
  }): Promise<ProposeFactorResult> {
    return await this.http.post<ProposeFactorResult>("/candidates", {
      expression: params.expression,
      hypothesis: params.hypothesis,
      name: params.name,
      proposed_by: params.proposedBy,
      venue: params.venue,
      symbol: params.symbol,
      timeframe: params.timeframe,
      test_results: params.testResults ?? {},
      batch_id: params.batchId,
      n_tested: params.nTested,
    });
  }

  /** D-12：列因子候选（status 过滤）。注意：没有 review 方法——register 门只在 UI。 */
  async listCandidates(params: {
    status?: "pending_review" | "registered" | "rejected";
    limit?: number;
  } = {}): Promise<FactorCandidateRecord[]> {
    return await this.http.get<FactorCandidateRecord[]>("/candidates", {
      status: params.status,
      limit: params.limit,
    });
  }
}
