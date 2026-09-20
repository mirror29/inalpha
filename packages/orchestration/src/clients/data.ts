/**
 * services/data 客户端。
 */
import { HttpClient, HttpClientError } from "./http.js";

export type Bar = {
  ts: string;
  venue: string;
  symbol: string;
  timeframe: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
};

export type BackfillResult = {
  venue: string;
  symbol: string;
  timeframe: string;
  bars_fetched: number;
  bars_inserted: number;
  from_ts: string;
  to_ts: string;
};

export type Ticker = {
  venue: string;
  symbol: string;
  price: number;
  ts: string;
  source: string;
  is_stale: boolean;
  stale_seconds: number;
};

export type NewsMarket =
  | "cn" | "us" | "hk" | "jp" | "kr" | "au" | "in" | "uk"
  | "de" | "fr" | "ca" | "br" | "global" | "crypto";

export type MarketEventType =
  | "listing"
  | "delisting"
  | "exploit"
  | "chain_halt"
  | "regulatory"
  | "upgrade"
  | "unlock"
  | "burn"
  | "partnership"
  | "macro"
  | "other";

export type EventSnapshotRecord = {
  snapshot_id: string;
  cutoff: string;
  policy_version: string;
  query_hash: string;
  events_sha256: string;
  coverage: Record<string, unknown>;
  event_types: MarketEventType[];
  assets: string[];
  asset_ids: string[];
  fact_count: number;
  created_at: string;
  facts: Array<Record<string, unknown>>;
};

export type AssetIdentity = {
  asset_id: string;
  venue: string;
  symbol: string;
  event_asset_code: string;
};

export class DataClient {
  private readonly http: HttpClient;

  constructor(options: { baseUrl: string; token: string; timeoutMs?: number }) {
    this.http = new HttpClient(options);
  }

  async health(): Promise<{ status: string; service: string; version: string; db: string }> {
    return await this.http.get("/health");
  }

  async getBars(params: {
    venue: string;
    symbol: string;
    timeframe: string;
    fromTs: string;
    toTs: string;
    limit?: number;
  }): Promise<Bar[]> {
    return await this.http.get<Bar[]>("/bars", {
      venue: params.venue,
      symbol: params.symbol,
      timeframe: params.timeframe,
      from_ts: params.fromTs,
      to_ts: params.toTs,
      limit: params.limit,
    });
  }

  async backfillBars(params: {
    venue: string;
    symbol: string;
    timeframe: string;
    fromTs: string;
    toTs: string;
  }): Promise<BackfillResult> {
    return await this.http.post<BackfillResult>("/backfill/bars", {
      venue: params.venue,
      symbol: params.symbol,
      timeframe: params.timeframe,
      from_ts: params.fromTs,
      to_ts: params.toTs,
    });
  }

  async getTicker(params: {
    venue: string;
    symbol: string;
    fresh?: boolean;
  }): Promise<Ticker> {
    return await this.http.get<Ticker>("/ticker", {
      venue: params.venue,
      symbol: params.symbol,
      fresh: params.fresh ?? false,
    });
  }

  async getFundamentals(params: {
    venue: string;
    symbol: string;
    asOf?: string;
  }): Promise<Record<string, unknown>> {
    try {
      return await this.http.get<Record<string, unknown>>("/fundamentals", {
        venue: params.venue,
        symbol: params.symbol,
        ...(params.asOf ? { as_of: params.asOf } : {}),
      });
    } catch (err) {
      if (err instanceof HttpClientError) {
        return { available: false, reason: `upstream ${err.status}` };
      }
      return { available: false, reason: String(err) };
    }
  }

  async getNews(params: {
    market?: NewsMarket;
    venue?: string;
    symbol?: string;
    asOf?: string;
    since?: string;
    kinds?: Array<"market_news" | "media" | "disclosure">;
    language?: string;
    limit?: number;
  }): Promise<Record<string, unknown>> {
    try {
      return await this.http.get<Record<string, unknown>>("/news", {
        ...(params.market ? { market: params.market } : {}),
        ...(params.venue ? { venue: params.venue } : {}),
        ...(params.symbol ? { symbol: params.symbol } : {}),
        ...(params.asOf ? { as_of: params.asOf } : {}),
        ...(params.since ? { since: params.since } : {}),
        ...(params.kinds?.length ? { kinds: params.kinds.join(",") } : {}),
        ...(params.language ? { language: params.language } : {}),
        limit: String(params.limit ?? 10),
      });
    } catch (err) {
      if (err instanceof HttpClientError && err.status < 500) {
        throw err;
      }
      return {
        market: params.market,
        symbol: params.symbol,
        items: [],
        providers: [],
        is_partial: true,
        error: err instanceof HttpClientError ? `upstream ${err.status}: ${err.message}` : String(err),
      };
    }
  }

  /** Freeze a deterministic point-in-time event set for one research campaign. */
  async createEventSnapshot(params: {
    cutoff: string;
    policyVersion: string;
    eventTypes?: MarketEventType[];
    assets?: string[];
    assetIds?: string[];
  }): Promise<EventSnapshotRecord> {
    return await this.http.post<EventSnapshotRecord>("/events/snapshots", {
      cutoff: params.cutoff,
      policy_version: params.policyVersion,
      event_types: params.eventTypes ?? [],
      assets: params.assets ?? [],
      asset_ids: params.assetIds ?? [],
    });
  }

  /** Resolve the Data-owned stable identity before freezing campaign inputs. */
  async resolveAsset(params: { venue: string; symbol: string }): Promise<AssetIdentity> {
    return await this.http.get<AssetIdentity>("/assets/resolve", params);
  }

  async getMarketNews(params: {
    market?: string;
    limit?: number;
  }): Promise<Record<string, unknown>> {
    return await this.getNews({
      market: (params.market ?? "cn") as NewsMarket,
      limit: params.limit ?? 20,
      kinds: ["market_news", "media"],
    });
  }

  async getMarketSectors(params: {
    market?: string;
    topN?: number;
  }): Promise<Record<string, unknown>> {
    try {
      return await this.http.get<Record<string, unknown>>("/market/sectors", {
        market: params.market ?? "cn",
        top_n: String(params.topN ?? 10),
      });
    } catch (err) {
      // 错误回落不带 top/bottom:空数组会误过前端 isSectorBoard 渲染出空板;
      // 只回 {market,error}(同 moneyflow),让视图守卫失败、回落通用 ToolOutput 显示错误。
      if (err instanceof HttpClientError) {
        return { market: params.market ?? "cn", error: `upstream ${err.status}: ${err.message}` };
      }
      return { market: params.market ?? "cn", error: String(err) };
    }
  }

  async getMarketMoneyflow(params: {
    market?: string;
  }): Promise<Record<string, unknown>> {
    try {
      return await this.http.get<Record<string, unknown>>("/market/moneyflow", {
        market: params.market ?? "cn",
      });
    } catch (err) {
      if (err instanceof HttpClientError) {
        return { market: params.market ?? "cn", error: `upstream ${err.status}: ${err.message}` };
      }
      return { market: params.market ?? "cn", error: String(err) };
    }
  }

  async getMarketMovers(params: {
    market?: string;
    limit?: number;
  }): Promise<Record<string, unknown>> {
    try {
      return await this.http.get<Record<string, unknown>>("/market/movers", {
        market: params.market ?? "cn",
        limit: String(params.limit ?? 30),
      });
    } catch (err) {
      if (err instanceof HttpClientError) {
        return { market: params.market ?? "cn", items: [], error: `upstream ${err.status}: ${err.message}` };
      }
      return { market: params.market ?? "cn", items: [], error: String(err) };
    }
  }

  async searchSymbols(params: {
    query: string;
    venue?: string;
    maxResults?: number;
  }): Promise<Record<string, unknown>> {
    try {
      return await this.http.get<Record<string, unknown>>("/symbols/search", {
        query: params.query,
        venue: params.venue ?? "auto",
        max_results: String(params.maxResults ?? 10),
      });
    } catch (err) {
      if (err instanceof HttpClientError) {
        return { query: params.query, results: [], error: `upstream ${err.status}` };
      }
      return { query: params.query, results: [], error: String(err) };
    }
  }
}
