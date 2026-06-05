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
  icir: number;
  sample_size: number;
  quantile_returns: { q: number; mean_return: number; sample_size: number }[];
  long_short_return: number;
  direction: number; // +1/-1/0
  strength: number; // 0-1
  low_confidence: boolean;
};

export type ScoreResult = {
  venue: string;
  symbol: string;
  timeframe: string;
  as_of: string;
  horizon_bars: number;
  bars_used: number;
  factors: FactorEffectiveness[];
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
