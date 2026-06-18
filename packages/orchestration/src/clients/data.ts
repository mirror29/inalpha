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

  async getMarketNews(params: {
    market?: string;
    limit?: number;
  }): Promise<Record<string, unknown>> {
    try {
      return await this.http.get<Record<string, unknown>>("/market/news", {
        market: params.market ?? "cn",
        limit: String(params.limit ?? 20),
      });
    } catch (err) {
      if (err instanceof HttpClientError) {
        return { market: params.market ?? "cn", items: [], error: `upstream ${err.status}: ${err.message}` };
      }
      return { market: params.market ?? "cn", items: [], error: String(err) };
    }
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
