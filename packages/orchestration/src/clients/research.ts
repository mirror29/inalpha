/**
 * services/research 客户端 —— 调 POST /deep_dive。
 *
 * 单端点小客户端，与 ``DataClient`` / ``PaperClient`` 同构。
 */
import { HttpClient } from "./http.js";

/** Factor —— 单一影响因子（D-8c 起结构化） */
export type Factor = {
  name: string;
  kind: "momentum" | "mean_reversion" | "volatility" | "macro" | "sentiment";
  value: number | string;
  strength: number;
  horizon: "intraday" | "swing" | "position";
  explanation: string;
};

/** Signal —— 因子合成的方向性信号 */
export type Signal = {
  direction: "long" | "short" | "flat";
  strength: number;
  timeframe: string;
  derived_from: string[];
};

/** StrategyHint —— 给 compose 引擎的机器消费提示 */
export type StrategyHint = {
  family: "trend" | "mean_reversion" | "buy_hold" | "none";
  params: Record<string, unknown>;
  reasoning: string;
};

/** AnalystBrief —— 与 services/research schemas.py 对齐 */
export type AnalystBrief = {
  analyst: "technical" | "fundamental" | "sentiment" | "risk" | "macro";
  stance: "bullish" | "bearish" | "neutral";
  confidence: number;
  summary: string;
  key_points: string[];
  factors: Factor[];
  raw_excerpt: string | null;
};

/** ResearchPlan —— 与 services/research schemas.py 对齐 */
export type ResearchPlan = {
  research_id: string;
  venue: string;
  symbol: string;
  timeframe: string;
  as_of: string; // ISO 8601
  rating: "overweight" | "neutral" | "underweight";
  confidence: number;
  thesis: string;
  risks: string[];
  suggested_action: string;
  factors: Factor[];
  signals: Signal[];
  strategy_hint: StrategyHint;
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

  constructor(opts: { baseUrl: string; token: string; timeoutMs?: number }) {
    // deep_dive 单次 30-90s（5 个 analyst 并行 LLM call + manager 综合），
    // 默认 120s 而不是 HttpClient 的 30s 默认（review B18，旧版恰好卡在边界）
    this.http = new HttpClient({
      baseUrl: opts.baseUrl,
      token: opts.token,
      timeoutMs: opts.timeoutMs ?? 120_000,
    });
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
