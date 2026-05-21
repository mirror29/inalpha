/**
 * services/data 客户端。
 */
import { HttpClient } from "./http.js";

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
}
