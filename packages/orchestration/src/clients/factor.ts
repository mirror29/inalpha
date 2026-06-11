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
}
