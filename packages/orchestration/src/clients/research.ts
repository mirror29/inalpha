/**
 * services/research 客户端 —— 调 POST /deep_dive。
 *
 * 单端点小客户端，与 ``DataClient`` / ``PaperClient`` 同构。
 */
import { HttpClient } from "./http.js";

/** AnalystBrief —— 与 services/research schemas.py 对齐 */
export type AnalystBrief = {
  analyst: "technical" | "fundamental";
  stance: "bullish" | "bearish" | "neutral";
  confidence: number;
  summary: string;
  key_points: string[];
  raw_excerpt: string | null;
};

/** ResearchPlan —— 与 services/research schemas.py 对齐 */
export type ResearchPlan = {
  venue: string;
  symbol: string;
  timeframe: string;
  as_of: string; // ISO 8601
  rating: "overweight" | "neutral" | "underweight";
  confidence: number;
  thesis: string;
  risks: string[];
  suggested_action: string;
  briefs: AnalystBrief[];
  horizon: "intraday" | "swing" | "position";
};

export type DeepDiveParams = {
  venue?: string;
  symbol: string;
  timeframe?: string;
  asOf: string; // ISO 8601
  lookbackDays?: number;
  userQuestion?: string;
};

export class ResearchClient {
  private readonly http: HttpClient;

  constructor(opts: { baseUrl: string; token: string }) {
    this.http = new HttpClient({ baseUrl: opts.baseUrl, token: opts.token });
  }

  async deepDive(params: DeepDiveParams): Promise<ResearchPlan> {
    return await this.http.post<ResearchPlan>("/deep_dive", {
      venue: params.venue ?? "binance",
      symbol: params.symbol,
      timeframe: params.timeframe ?? "1h",
      as_of: params.asOf,
      lookback_days: params.lookbackDays ?? 30,
      user_question: params.userQuestion,
    });
  }

  async health(): Promise<{ status: string; service: string; version: string; llm_provider: string }> {
    return await this.http.get<{
      status: string;
      service: string;
      version: string;
      llm_provider: string;
    }>("/health");
  }
}
