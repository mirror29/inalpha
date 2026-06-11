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

  async getFundamentals(params: { venue: string; symbol: string }): Promise<Record<string, unknown>> {
    try {
      return await this.http.get<Record<string, unknown>>("/fundamentals", {
        venue: params.venue,
        symbol: params.symbol,
      });
    } catch (err) {
      if (err instanceof HttpClientError) {
        return { available: false, reason: `upstream ${err.status}` };
      }
      return { available: false, reason: String(err) };
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
